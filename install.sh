#!/bin/bash
# ─────────────────────────────────────────────
# sorc — Screen Orchestrator
# Install / Update script
# https://github.com/TheDynomike/sorc
# ─────────────────────────────────────────────
set -euo pipefail

REPO_OWNER="TheDynomike"
REPO_NAME="sorc"
REPO_BRANCH="main"
REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"
RAW_BASE="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"
API_BASE="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}"

INSTALL_BIN="/usr/local/bin/sorc"
SORC_DIR="$HOME/.sorc"
LOG_FILE="/tmp/sorc-install-$(date +%Y%m%dT%H%M%S).log"

INSTALLED=()
FAILED=()
WARNED=()
IS_UPDATE=0

# ── colors ──
RED='\033[0;91m'; GREEN='\033[0;92m'; YELLOW='\033[0;93m'
BLUE='\033[0;94m'; CYAN='\033[0;96m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

# ── logging helpers ──
_log()   { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG_FILE"; }
info()   { echo -e "  ${BLUE}→${RESET} $1";                    _log "INFO  $1"; }
ok()     { echo -e "  ${GREEN}✓${RESET} $1";                    _log "OK    $1"; }
warn()   { echo -e "  ${YELLOW}!${RESET} $1"; WARNED+=("$1");   _log "WARN  $1"; }
fail()   { echo -e "  ${RED}✗${RESET} $1";   FAILED+=("$1");    _log "FAIL  $1"; }
detail() { echo -e "  ${DIM}  $1${RESET}";                      _log "      $1"; }
step()   { echo -e "\n${BOLD}$1${RESET}\n$(printf '─%.0s' {1..46})"; _log "STEP  $1"; }
die()    {
  echo -e "\n  ${RED}${BOLD}✗ Fatal:${RESET} $1\n" >&2
  _log "FATAL $1"
  echo -e "  ${DIM}Full log: $LOG_FILE${RESET}\n" >&2
  exit 1
}

# ── package manager detection ──
detect_pm() {
  if   command -v apt-get &>/dev/null; then echo "apt"
  elif command -v dnf     &>/dev/null; then echo "dnf"
  elif command -v yum     &>/dev/null; then echo "yum"
  elif command -v pacman  &>/dev/null; then echo "pacman"
  elif command -v zypper  &>/dev/null; then echo "zypper"
  elif command -v brew    &>/dev/null; then echo "brew"
  else echo "unknown"
  fi
}

PM=$(detect_pm)

install_hint() {
  case "$PM" in
    apt)    echo "sudo apt-get install -y $1" ;;
    dnf)    echo "sudo dnf install -y $1" ;;
    yum)    echo "sudo yum install -y $1" ;;
    pacman) echo "sudo pacman -S --noconfirm $1" ;;
    zypper) echo "sudo zypper install -y $1" ;;
    brew)   echo "brew install $1" ;;
    *)      echo "install $1 manually for your distro" ;;
  esac
}

try_install() {
  local pkg="$1" label="${2:-$1}"
  info "Installing $label..."
  detail "Package manager: $PM  |  package: $pkg"
  local ok_flag=0
  case "$PM" in
    apt)    sudo apt-get install -y "$pkg"       >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    dnf)    sudo dnf install -y "$pkg"           >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    yum)    sudo yum install -y "$pkg"           >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    pacman) sudo pacman -S --noconfirm "$pkg"    >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    zypper) sudo zypper install -y "$pkg"        >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    brew)   brew install "$pkg"                  >> "$LOG_FILE" 2>&1 && ok_flag=1 ;;
    *)      detail "Unknown package manager — skipping auto-install" ;;
  esac
  return $((1 - ok_flag))
}

ensure_dep() {
  local bin="$1" pkg="${2:-$1}" label="${3:-$1}" hard="${4:-0}"

  if command -v "$bin" &>/dev/null; then
    local ver; ver=$("$bin" --version 2>&1 | head -1 || true)
    ok "$label — $ver"
    return 0
  fi

  warn "$label not found"

  if [ "$hard" = "1" ]; then
    if [ "$PM" = "unknown" ]; then
      die "$label is required.\n  Install manually: $(install_hint "$pkg")"
    fi
    if try_install "$pkg" "$label"; then
      INSTALLED+=("$label")
      ok "$label installed successfully"
    else
      die "Failed to install $label.\n  Manual: $(install_hint "$pkg")\n  Log: $LOG_FILE"
    fi
  else
    if [ "$PM" = "unknown" ]; then
      warn "Cannot auto-install. Manual: $(install_hint "$pkg")"
      return 1
    fi
    echo -ne "  ${CYAN}?${RESET} Auto-install $label? [Y/n] "
    read -r answer </dev/tty || answer="y"
    if [[ "$answer" =~ ^[Nn] ]]; then
      warn "Skipped $label — some sorc features may not work"
      return 1
    fi
    if try_install "$pkg" "$label"; then
      INSTALLED+=("$label")
      ok "$label installed"
    else
      fail "Could not auto-install $label"
      warn "Manual: $(install_hint "$pkg")"
      return 1
    fi
  fi
}

# ─────────────────────────────────────────────
# GITHUB HELPERS
# ─────────────────────────────────────────────

# Fetch a URL with curl or wget, output to stdout
http_get() {
  local url="$1"
  if command -v curl &>/dev/null; then
    curl -fsSL "$url"
  elif command -v wget &>/dev/null; then
    wget -qO- "$url"
  else
    die "curl or wget is required to download sorc. Install one and retry."
  fi
}

# Get the latest commit SHA on REPO_BRANCH via GitHub API
get_remote_sha() {
  http_get "${API_BASE}/commits/${REPO_BRANCH}" 2>>"$LOG_FILE" \
    | grep '"sha"' | head -1 \
    | sed 's/.*"sha": *"\([^"]*\)".*/\1/'
}

# Read locally cached SHA (written after each install/update)
get_local_sha() {
  local sha_file="$SORC_DIR/.installed_sha"
  [ -f "$sha_file" ] && cat "$sha_file" || echo ""
}

save_local_sha() {
  echo "$1" > "$SORC_DIR/.installed_sha"
}

# ─────────────────────────────────────────────
# BEGIN
# ─────────────────────────────────────────────

echo -e "\n${BOLD}sorc — Screen Orchestrator${RESET}  ${DIM}install/update${RESET}"
echo    "──────────────────────────────────────────────"
echo -e "  ${DIM}Repo:  $REPO_URL${RESET}"
echo -e "  ${DIM}Log:   $LOG_FILE${RESET}"
_log "Started. PM=$PM  OS=$(uname -sr)  USER=$(whoami)"

# ─────────────────────────────────────────────
step "1/5  Preflight"
# ─────────────────────────────────────────────

[[ "$(uname)" != "Linux" ]] && die "sorc requires Linux + systemd. Detected: $(uname)"
ok "Linux $(uname -r)"

command -v systemctl &>/dev/null || die "systemd not found. sorc is systemd-only."
ok "systemd — $(systemctl --version | head -1)"

[ "$EUID" -eq 0 ] && warn "Running as root — pods will run as root. A dedicated user is safer."

command -v sudo &>/dev/null || die "sudo is required."
ok "sudo available"

# curl or wget needed for download
if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
  ensure_dep curl curl "curl" 1
fi
ok "HTTP client — $(command -v curl &>/dev/null && echo curl || echo wget)"

# ─────────────────────────────────────────────
step "2/5  Check for updates"
# ─────────────────────────────────────────────

LOCAL_SHA=$(get_local_sha)
REMOTE_SHA=""

info "Fetching latest commit SHA from GitHub..."
if REMOTE_SHA=$(get_remote_sha) && [ -n "$REMOTE_SHA" ]; then
  detail "Remote SHA: ${REMOTE_SHA:0:12}"
  detail "Local  SHA: ${LOCAL_SHA:0:12}${LOCAL_SHA:+""}"
else
  warn "Could not reach GitHub API — proceeding with local install if sorc.py is present"
  REMOTE_SHA=""
fi

# Determine mode
if ! command -v sorc &>/dev/null && [ ! -f "$INSTALL_BIN" ]; then
  info "sorc binary missing (not found in PATH or $INSTALL_BIN) — forcing install"
  IS_UPDATE=0
elif [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
  ok "Already up to date (${REMOTE_SHA:0:12})"
  echo -e "\n  ${GREEN}${BOLD}sorc is current. Nothing to do.${RESET}"
  echo -e "  Run ${BLUE}sorc doctor${RESET} to verify your environment.\n"
  exit 0
elif [ -n "$LOCAL_SHA" ] || command -v sorc &>/dev/null; then
  CURRENT_VER=$(sorc --version 2>/dev/null || echo "unknown")
  info "Update available — current: $CURRENT_VER  remote: ${REMOTE_SHA:0:12}"
  IS_UPDATE=1
else
  info "Installing sorc for the first time"
  IS_UPDATE=0
fi

# ─────────────────────────────────────────────
step "3/5  Required dependencies"
# ─────────────────────────────────────────────

ensure_dep python3 python3 "Python 3" 1

PY_VER=$(python3 -c 'import sys; print(sys.version_info.major * 10 + sys.version_info.minor)')
if [ "$PY_VER" -lt 38 ]; then
  die "Python 3.8+ required. Found: $(python3 --version)"
fi
detail "Python version OK (≥3.8)"

ensure_dep journalctl systemd "journalctl" 1
ensure_dep git git "git" 1

# ─────────────────────────────────────────────
step "4/5  Optional dependencies"
# ─────────────────────────────────────────────

echo -e "  ${DIM}sorc needs screen or tmux (or both) for terminal sessions.${RESET}\n"

HAS_SCREEN=0; HAS_TMUX=0
command -v screen &>/dev/null && { ok "screen — $(screen --version 2>&1 | head -1)"; HAS_SCREEN=1; } \
  || { ensure_dep screen screen "GNU screen" 0 && HAS_SCREEN=1 || true; }

command -v tmux &>/dev/null && { ok "tmux — $(tmux -V)"; HAS_TMUX=1; } \
  || { ensure_dep tmux tmux "tmux" 0 && HAS_TMUX=1 || true; }

[ "$HAS_SCREEN" -eq 0 ] && [ "$HAS_TMUX" -eq 0 ] && \
  die "sorc requires screen or tmux. Neither installed.\n  $(install_hint screen)\n  $(install_hint tmux)"

command -v ss &>/dev/null \
  && ok "ss (iproute2) — available" \
  || { ensure_dep ss iproute2 "iproute2/ss" 0 || warn "sorc doctor port-scan checks will be skipped"; }

# ─────────────────────────────────────────────
step "5/5  Download & install sorc"
# ─────────────────────────────────────────────

SORC_PY_URL="${RAW_BASE}/sorc.py"
TMP_PY=$(mktemp /tmp/sorc-XXXXXX.py)
trap 'rm -f "$TMP_PY"' EXIT

# Download sorc.py (prefer GitHub, fall back to local copy in same dir)
if [ -n "$REMOTE_SHA" ]; then
  info "Downloading sorc.py from GitHub (${REMOTE_SHA:0:12})..."
  detail "URL: $SORC_PY_URL"
  if http_get "$SORC_PY_URL" > "$TMP_PY" 2>>"$LOG_FILE"; then
    ok "Downloaded sorc.py ($(wc -c < "$TMP_PY") bytes)"
  else
    warn "GitHub download failed — falling back to local sorc.py"
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -f "$SCRIPT_DIR/sorc.py" ] || die "No local sorc.py found and GitHub download failed."
    cp "$SCRIPT_DIR/sorc.py" "$TMP_PY"
    ok "Using local sorc.py"
    REMOTE_SHA=""  # don't cache SHA if we didn't download from GitHub
  fi
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$SCRIPT_DIR/sorc.py" ]; then
    info "Using local sorc.py (GitHub unreachable)"
    cp "$SCRIPT_DIR/sorc.py" "$TMP_PY"
  else
    die "Cannot reach GitHub and no local sorc.py found."
  fi
fi

# Verify it's valid Python before touching the live install
info "Verifying downloaded file..."
if python3 -m py_compile "$TMP_PY" 2>>"$LOG_FILE"; then
  ok "Python syntax valid"
else
  die "Downloaded sorc.py failed syntax check — aborting to protect existing install.\n  Log: $LOG_FILE"
fi

# Backup existing install if updating
if [ "$IS_UPDATE" = "1" ] && [ -f "$INSTALL_BIN" ]; then
  BACKUP="${INSTALL_BIN}.bak"
  sudo cp "$INSTALL_BIN" "$BACKUP" 2>>"$LOG_FILE" || true
  detail "Backup saved → $BACKUP"
fi

# Install
chmod +x "$TMP_PY"
if sudo cp "$TMP_PY" "$INSTALL_BIN" 2>>"$LOG_FILE" && sudo chmod +x "$INSTALL_BIN"; then
  ok "Installed → $INSTALL_BIN"
  detail "$(ls -lh "$INSTALL_BIN")"
else
  warn "sudo install to $INSTALL_BIN failed — falling back to ~/.local/bin"
  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"
  cp "$TMP_PY" "$LOCAL_BIN/sorc"
  chmod +x "$LOCAL_BIN/sorc"
  INSTALL_BIN="$LOCAL_BIN/sorc"
  ok "Installed → $INSTALL_BIN"
  if ! echo "$PATH" | grep -q "$LOCAL_BIN"; then
    warn "$LOCAL_BIN is not on your \$PATH"
    detail "Add to ~/.bashrc or ~/.zshrc:"
    echo   "         export PATH=\"\$HOME/.local/bin:\$PATH\""
    WARNED+=("~/.local/bin not on PATH — sorc may not be found in new shells")
  fi
fi

# Init ~/.sorc dirs
info "Initialising ~/.sorc..."
mkdir -p "$SORC_DIR"/{pods,logs,snapshots,templates}
detail "pods/ logs/ snapshots/ templates/ ready"
ok "~/.sorc ready"

# Cache the installed SHA
if [ -n "$REMOTE_SHA" ]; then
  save_local_sha "$REMOTE_SHA"
  detail "Cached SHA: ${REMOTE_SHA:0:12} → $SORC_DIR/.installed_sha"
fi

# Smoke test
info "Running smoke test..."
if SMOKE=$("$INSTALL_BIN" --version 2>&1); then
  ok "Smoke test passed — $SMOKE"
else
  # If we have a backup, roll back
  if [ "$IS_UPDATE" = "1" ] && [ -f "${INSTALL_BIN}.bak" ]; then
    warn "Smoke test failed — rolling back to previous version"
    sudo cp "${INSTALL_BIN}.bak" "$INSTALL_BIN" 2>/dev/null || cp "${INSTALL_BIN}.bak" "$INSTALL_BIN"
    die "Update rolled back. Previous version restored.\n  Log: $LOG_FILE"
  fi
  die "Installed binary failed smoke test.\n  Try: python3 $INSTALL_BIN --version\n  Log: $LOG_FILE"
fi

# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────

echo -e "\n${BOLD}Summary${RESET}\n$(printf '─%.0s' {1..46})"

[ ${#INSTALLED[@]} -gt 0 ] && {
  echo -e "  ${GREEN}Newly installed:${RESET}"
  for p in "${INSTALLED[@]}"; do detail "  + $p"; done; echo; }

[ ${#FAILED[@]} -gt 0 ] && {
  echo -e "  ${RED}Failed to install:${RESET}"
  for p in "${FAILED[@]}"; do detail "  ✗ $p"; done; echo; }

[ ${#WARNED[@]} -gt 0 ] && {
  echo -e "  ${YELLOW}Warnings:${RESET}"
  for w in "${WARNED[@]}"; do detail "  ! $w"; done; echo; }

[ -n "$REMOTE_SHA" ] && detail "Installed commit: ${REMOTE_SHA:0:12}  ($REPO_URL)"
echo -e "  ${DIM}Full log: $LOG_FILE${RESET}"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo -e "\n  ${YELLOW}${BOLD}sorc installed with warnings.${RESET} Some features may be limited.\n"
elif [ "$IS_UPDATE" = "1" ]; then
  echo -e "\n  ${GREEN}${BOLD}sorc updated successfully.${RESET}\n"
else
  echo -e "\n  ${GREEN}${BOLD}sorc installed successfully.${RESET}\n"
fi

echo -e "  Get started:"
echo -e "    ${BLUE}sorc doctor${RESET}          — verify your environment"
echo -e "    ${BLUE}sorc create <name>${RESET}    — create your first pod"
echo -e "    ${BLUE}sorc list${RESET}             — list all pods"
echo -e "\n  Re-run this script anytime to update sorc.\n"
