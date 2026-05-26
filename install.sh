#!/bin/bash
# ─────────────────────────────────────────────
# sorc install script
# Installs sorc + all required/optional deps
# ─────────────────────────────────────────────
set -euo pipefail

INSTALL_BIN="/usr/local/bin/sorc"
SORC_DIR="$HOME/.sorc"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SORC_PY="$SCRIPT_DIR/sorc.py"
LOG_FILE="/tmp/sorc-install-$(date +%Y%m%dT%H%M%S).log"
INSTALLED=()
FAILED=()
WARNED=()

# ── colors ──
RED='\033[0;91m'; GREEN='\033[0;92m'; YELLOW='\033[0;93m'
BLUE='\033[0;94m'; CYAN='\033[0;96m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

# ── logging helpers ──
_log()  { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG_FILE"; }
info()  { echo -e "  ${BLUE}→${RESET} $1";                   _log "INFO  $1"; }
ok()    { echo -e "  ${GREEN}✓${RESET} $1";                   _log "OK    $1"; }
warn()  { echo -e "  ${YELLOW}!${RESET} $1"; WARNED+=("$1");  _log "WARN  $1"; }
fail()  { echo -e "  ${RED}✗${RESET} $1"; FAILED+=("$1");     _log "FAIL  $1"; }
die()   { echo -e "\n  ${RED}${BOLD}✗ Fatal:${RESET} $1\n" >&2; _log "FATAL $1"
          echo -e "  ${DIM}Full log: $LOG_FILE${RESET}\n" >&2; exit 1; }
step()  { echo -e "\n${BOLD}$1${RESET}\n$(printf '─%.0s' {1..46})"; _log "STEP  $1"; }
detail(){ echo -e "  ${DIM}  $1${RESET}"; _log "      $1"; }

# ── package manager detection ──
detect_pm() {
  if command -v apt-get &>/dev/null;  then echo "apt"
  elif command -v dnf &>/dev/null;    then echo "dnf"
  elif command -v yum &>/dev/null;    then echo "yum"
  elif command -v pacman &>/dev/null; then echo "pacman"
  elif command -v zypper &>/dev/null; then echo "zypper"
  elif command -v brew &>/dev/null;   then echo "brew"
  else echo "unknown"
  fi
}

PM=$(detect_pm)

install_cmd() {
  local pkg="$1"
  case "$PM" in
    apt)    echo "sudo apt-get install -y $pkg" ;;
    dnf)    echo "sudo dnf install -y $pkg" ;;
    yum)    echo "sudo yum install -y $pkg" ;;
    pacman) echo "sudo pacman -S --noconfirm $pkg" ;;
    zypper) echo "sudo zypper install -y $pkg" ;;
    brew)   echo "brew install $pkg" ;;
    *)      echo "# install $pkg manually" ;;
  esac
}

# Try to install a package; returns 0 on success, 1 on failure
try_install() {
  local pkg="$1"
  local label="${2:-$pkg}"
  info "Installing $label..."
  detail "Package manager: $PM"

  local cmd
  case "$PM" in
    apt)
      detail "Running: sudo apt-get install -y $pkg"
      if sudo apt-get install -y "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    dnf)
      detail "Running: sudo dnf install -y $pkg"
      if sudo dnf install -y "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    yum)
      detail "Running: sudo yum install -y $pkg"
      if sudo yum install -y "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    pacman)
      detail "Running: sudo pacman -S --noconfirm $pkg"
      if sudo pacman -S --noconfirm "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    zypper)
      detail "Running: sudo zypper install -y $pkg"
      if sudo zypper install -y "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    brew)
      detail "Running: brew install $pkg"
      if brew install "$pkg" >> "$LOG_FILE" 2>&1; then return 0; fi ;;
    *)
      detail "Unknown package manager — cannot auto-install" ;;
  esac
  return 1
}

# Ensure a dep is present; hard=1 means fatal if missing & can't install
ensure_dep() {
  local bin="$1"
  local pkg="${2:-$1}"
  local label="${3:-$bin}"
  local hard="${4:-0}"

  if command -v "$bin" &>/dev/null; then
    local ver
    ver=$("$bin" --version 2>&1 | head -1 || true)
    ok "$label — $ver"
    return 0
  fi

  warn "$label not found"

  if [ "$PM" = "unknown" ]; then
    local msg="Could not auto-install $label. $(install_cmd "$pkg")"
    if [ "$hard" = "1" ]; then die "$msg"; else warn "$msg"; fi
    return 1
  fi

  if [ "$hard" = "1" ]; then
    # Required — attempt install, die on failure
    if try_install "$pkg" "$label"; then
      INSTALLED+=("$label")
      ok "$label installed"
    else
      die "Failed to install $label. Check log: $LOG_FILE\n  Manual: $(install_cmd "$pkg")"
    fi
  else
    # Optional — attempt install, warn on failure
    echo -ne "  ${CYAN}?${RESET} Auto-install $label? [Y/n] "
    read -r answer </dev/tty
    if [[ "$answer" =~ ^[Nn] ]]; then
      warn "Skipped $label — some sorc features may not work"
      return 1
    fi
    if try_install "$pkg" "$label"; then
      INSTALLED+=("$label")
      ok "$label installed"
    else
      fail "Failed to auto-install $label"
      warn "Manual install: $(install_cmd "$pkg")"
    fi
  fi
}

# ─────────────────────────────────────────────
# BEGIN
# ─────────────────────────────────────────────

echo -e "\n${BOLD}sorc — Screen Orchestrator  ${DIM}install script${RESET}"
echo    "──────────────────────────────────────────────"
echo -e "  ${DIM}Log: $LOG_FILE${RESET}"
_log "Install started. PM=$PM OS=$(uname -sr)"

# ─────────────────────────────────────────────
step "1/5  Preflight"
# ─────────────────────────────────────────────

# Must be Linux
if [[ "$(uname)" != "Linux" ]]; then
  die "sorc requires Linux (systemd). Detected: $(uname)"
fi
ok "Linux $(uname -r)"

# sorc.py must exist
if [ ! -f "$SORC_PY" ]; then
  die "sorc.py not found at $SORC_PY\n  Run install.sh from the same directory as sorc.py"
fi
ok "sorc.py found at $SORC_PY"

# Not running as root (should use sudo selectively)
if [ "$EUID" -eq 0 ]; then
  warn "Running as root — sorc pods will run as root. Consider a dedicated user."
fi

# sudo available
if ! command -v sudo &>/dev/null; then
  die "sudo is required to install system deps and write to /usr/local/bin"
fi
ok "sudo available"

# ─────────────────────────────────────────────
step "2/5  Required dependencies"
# ─────────────────────────────────────────────

# python3 — hard required
ensure_dep python3 python3 "Python 3" 1

# Python version check
PY_VER=$(python3 -c 'import sys; print(sys.version_info.major * 10 + sys.version_info.minor)')
if [ "$PY_VER" -lt 38 ]; then
  die "Python 3.8+ required. Found: $(python3 --version)\n  Upgrade Python and re-run."
fi
detail "Version OK (3.8+ required)"

# systemd — hard required
if ! command -v systemctl &>/dev/null; then
  die "systemd is required. sorc manages agents via systemd units.\n  sorc does not support non-systemd Linux."
fi
SYSTEMD_VER=$(systemctl --version | head -1)
ok "systemd — $SYSTEMD_VER"

# journalctl — hard required (part of systemd, but verify)
ensure_dep journalctl systemd "journalctl" 1

# git — hard required (pod source management)
ensure_dep git git "git" 1

# ─────────────────────────────────────────────
step "3/5  Optional dependencies"
# ─────────────────────────────────────────────

echo -e "  ${DIM}sorc needs screen or tmux (or both) for terminal sessions.${RESET}\n"

HAS_SCREEN=0; HAS_TMUX=0

if command -v screen &>/dev/null; then
  ok "screen — $(screen --version 2>&1 | head -1)"
  HAS_SCREEN=1
else
  if ensure_dep screen screen "GNU screen" 0; then HAS_SCREEN=1; fi
fi

if command -v tmux &>/dev/null; then
  ok "tmux — $(tmux -V)"
  HAS_TMUX=1
else
  if ensure_dep tmux tmux "tmux" 0; then HAS_TMUX=1; fi
fi

if [ "$HAS_SCREEN" -eq 0 ] && [ "$HAS_TMUX" -eq 0 ]; then
  die "sorc requires screen or tmux. Neither could be installed.\n  $(install_cmd screen)\n  $(install_cmd tmux)"
fi

# ss (iproute2) — for sorc doctor port scanning
if ! command -v ss &>/dev/null; then
  info "ss (iproute2) not found — used by sorc doctor for port conflict detection"
  ensure_dep ss iproute2 "iproute2/ss" 0 || warn "sorc doctor port checks will be skipped"
else
  ok "ss (iproute2) — available"
fi

# ─────────────────────────────────────────────
step "4/5  Installing sorc"
# ─────────────────────────────────────────────

chmod +x "$SORC_PY"
detail "Set executable: $SORC_PY"

if sudo cp "$SORC_PY" "$INSTALL_BIN" 2>>"$LOG_FILE" && sudo chmod +x "$INSTALL_BIN"; then
  ok "Installed → $INSTALL_BIN"
  detail "$(ls -lh "$INSTALL_BIN")"
else
  warn "sudo install to $INSTALL_BIN failed — falling back to user-local"
  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"
  cp "$SORC_PY" "$LOCAL_BIN/sorc"
  chmod +x "$LOCAL_BIN/sorc"
  INSTALL_BIN="$LOCAL_BIN/sorc"
  ok "Installed → $INSTALL_BIN"

  if ! echo "$PATH" | grep -q "$LOCAL_BIN"; then
    warn "$LOCAL_BIN is not on your \$PATH"
    detail "Add to ~/.bashrc or ~/.zshrc:"
    echo   "         export PATH=\"\$HOME/.local/bin:\$PATH\""
    WARNED+=("~/.local/bin not on PATH")
  fi
fi

# Init ~/.sorc dirs
info "Initialising ~/.sorc directories..."
mkdir -p "$SORC_DIR"/{pods,logs,snapshots,templates}
detail "Created: pods/ logs/ snapshots/ templates/"
ok "~/.sorc ready"

# Smoke test
info "Running smoke test..."
if SMOKE=$("$INSTALL_BIN" --version 2>&1); then
  ok "Smoke test passed — $SMOKE"
else
  die "Binary installed but failed to run.\n  Try: python3 $INSTALL_BIN --version\n  Log: $LOG_FILE"
fi

# ─────────────────────────────────────────────
step "5/5  Summary"
# ─────────────────────────────────────────────

if [ ${#INSTALLED[@]} -gt 0 ]; then
  echo -e "  ${GREEN}Packages installed:${RESET}"
  for pkg in "${INSTALLED[@]}"; do detail "$pkg"; done
  echo
fi

if [ ${#FAILED[@]} -gt 0 ]; then
  echo -e "  ${RED}Packages that failed to install:${RESET}"
  for pkg in "${FAILED[@]}"; do detail "✗ $pkg"; done
  echo
fi

if [ ${#WARNED[@]} -gt 0 ]; then
  echo -e "  ${YELLOW}Warnings:${RESET}"
  for w in "${WARNED[@]}"; do detail "! $w"; done
  echo
fi

echo -e "  ${DIM}Full install log: $LOG_FILE${RESET}"

# Final verdict
if [ ${#FAILED[@]} -gt 0 ]; then
  echo -e "\n  ${YELLOW}${BOLD}sorc installed with warnings.${RESET} Some features may be limited.\n"
else
  echo -e "\n  ${GREEN}${BOLD}sorc installed successfully.${RESET}\n"
fi

echo -e "  Get started:"
echo -e "    ${BLUE}sorc doctor${RESET}          — verify your environment"
echo -e "    ${BLUE}sorc create <name>${RESET}    — create your first pod"
echo -e "    ${BLUE}sorc list${RESET}             — list all pods"
echo
