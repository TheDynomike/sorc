#!/usr/bin/env python3
"""
sorc.py — Screen Orchestrator
Single-file, zero-dependency agent manager using systemd + screen/tmux.
"""

# ─────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────

import argparse, json, subprocess, os, sys, shutil, signal
import pwd, grp, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

SORC_DIR    = Path.home() / ".sorc"
PODS_DIR    = SORC_DIR / "pods"
LOGS_DIR    = SORC_DIR / "logs"
SNAP_DIR    = SORC_DIR / "snapshots"
VERSION     = "0.1.0"
LOG_MAX_MB  = 10
LOG_MAX_ROT = 5

EXIT_OK         = 0
EXIT_NOT_FOUND  = 1
EXIT_SYSTEMD    = 2
EXIT_HEALTH     = 3
EXIT_DEP        = 4
EXIT_MANIFEST   = 5

# ─────────────────────────────────────────────
# HELPERS — FILESYSTEM
# ─────────────────────────────────────────────

def ensure_dirs():
    for d in [SORC_DIR, PODS_DIR, LOGS_DIR, SNAP_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def pod_dir(name: str) -> Path:
    return PODS_DIR / name

def pod_manifest(name: str) -> Path:
    return pod_dir(name) / "pod.json"

def pod_exists(name: str) -> bool:
    return pod_manifest(name).exists()

def load_manifest(name: str) -> dict:
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)
    with open(pod_manifest(name)) as f:
        return json.load(f)

def save_manifest(name: str, data: dict):
    with open(pod_manifest(name), "w") as f:
        json.dump(data, f, indent=2)

def write_script(path: Path, content: str):
    path.write_text(content)
    path.chmod(0o755)

# ─────────────────────────────────────────────
# HELPERS — OUTPUT
# ─────────────────────────────────────────────

def info(msg):  print(f"  \033[94m→\033[0m {msg}")
def ok(msg):    print(f"  \033[92m✓\033[0m {msg}")
def warn(msg):  print(f"  \033[93m!\033[0m {msg}")
def err(msg):   print(f"  \033[91m✗\033[0m {msg}", file=sys.stderr)
def die(msg, code=1):
    err(msg)
    sys.exit(code)

def header(title):
    print(f"\n\033[1m{title}\033[0m")
    print("─" * 48)

def table(rows, headers):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("─" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))

# ─────────────────────────────────────────────
# HELPERS — SHELL
# ─────────────────────────────────────────────

def run(cmd, check=True, capture=False, sudo=False):
    if sudo:
        cmd = ["sudo"] + cmd
    kwargs = dict(check=check)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return subprocess.run(cmd, **kwargs)
    except subprocess.CalledProcessError as e:
        die(f"Command failed: {' '.join(cmd)}\n{e.stderr or ''}", EXIT_SYSTEMD)
    except FileNotFoundError:
        die(f"Executable not found: {cmd[0]}", EXIT_SYSTEMD)

def run_out(cmd, default="", sudo=False) -> str:
    if sudo:
        cmd = ["sudo"] + cmd
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, check=False)
        return r.stdout.strip()
    except Exception:
        return default

def which(cmd) -> bool:
    return shutil.which(cmd) is not None

# ─────────────────────────────────────────────
# HELPERS — SYSTEMD
# ─────────────────────────────────────────────

def svc_name(pod_name: str) -> str:
    return f"sorc-{pod_name}"

def unit_path(pod_name: str) -> Path:
    return Path(f"/etc/systemd/system/{svc_name(pod_name)}.service")

def systemctl(action: str, pod_name: str, sudo=True) -> bool:
    r = subprocess.run(
        (["sudo"] if sudo else []) + ["systemctl", action, svc_name(pod_name)],
        check=False
    )
    return r.returncode == 0

def is_active(pod_name: str) -> bool:
    return run_out(["systemctl", "is-active", svc_name(pod_name)]) == "active"

def is_failed(pod_name: str) -> bool:
    return run_out(["systemctl", "is-active", svc_name(pod_name)]) == "failed"

def daemon_reload():
    run(["systemctl", "daemon-reload"], sudo=True)

# ─────────────────────────────────────────────
# HELPERS — SORC LOGGING
# ─────────────────────────────────────────────

def sorc_log(pod_name: str, event: str, extra: dict = None):
    log_file = LOGS_DIR / f"{pod_name}.jsonl"
    entry = {"timestamp": datetime.now().isoformat(), "event": event, "pod": pod_name}
    if extra:
        entry.update(extra)
    # Rotate if needed
    try:
        if log_file.exists() and log_file.stat().st_size > LOG_MAX_MB * 1024 * 1024:
            for i in range(LOG_MAX_ROT - 1, 0, -1):
                src = Path(f"{log_file}.{i}")
                dst = Path(f"{log_file}.{i+1}")
                if src.exists():
                    src.rename(dst)
            log_file.rename(Path(f"{log_file}.1"))
    except Exception:
        pass
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

# ─────────────────────────────────────────────
# TERMINAL BACKEND ABSTRACTION
# ─────────────────────────────────────────────

class ScreenBackend:
    def session_name(self, pod_name): return f"sorc-{pod_name}"

    def session_exists(self, pod_name) -> bool:
        out = run_out(["screen", "-ls"])
        return self.session_name(pod_name) in out

    def create_cmd(self, pod_name, launch_script) -> list:
        return ["screen", "-dmS", self.session_name(pod_name), "/bin/bash", str(launch_script)]

    def attach_cmd(self, pod_name) -> list:
        return ["screen", "-r", self.session_name(pod_name)]

    def kill_cmd(self, pod_name) -> list:
        return ["screen", "-S", self.session_name(pod_name), "-X", "quit"]

    def detach_hint(self) -> str:
        return "Ctrl+A, D  to detach  |  Ctrl+C  kills the process"


class TmuxBackend:
    def session_name(self, pod_name): return f"sorc-{pod_name}"

    def session_exists(self, pod_name) -> bool:
        r = subprocess.run(["tmux", "has-session", "-t", self.session_name(pod_name)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0

    def create_cmd(self, pod_name, launch_script) -> list:
        return ["tmux", "new-session", "-d", "-s", self.session_name(pod_name),
                "/bin/bash", str(launch_script)]

    def attach_cmd(self, pod_name) -> list:
        return ["tmux", "attach-session", "-t", self.session_name(pod_name)]

    def kill_cmd(self, pod_name) -> list:
        return ["tmux", "kill-session", "-t", self.session_name(pod_name)]

    def detach_hint(self) -> str:
        return "Ctrl+B, D  to detach  |  Ctrl+C  kills the process"


def get_backend(manifest: dict):
    backend = manifest.get("terminal", {}).get("backend", "screen")
    if backend == "tmux":
        return TmuxBackend()
    return ScreenBackend()

# ─────────────────────────────────────────────
# SCRIPT GENERATION
# ─────────────────────────────────────────────

def source_type(manifest: dict) -> str:
    """Return 'remote', 'local', or 'none' based on the source block."""
    src = manifest.get("source", {})
    if src.get("git_repo"):   return "remote"
    if src.get("local_path"): return "local"
    return "none"


def resolve_working_dir(manifest: dict, pd: Path) -> str:
    """Return the effective working directory for launch.sh."""
    run_cfg = manifest.get("run", {})
    explicit = run_cfg.get("working_dir", "")
    if explicit:
        return explicit
    stype = source_type(manifest)
    if stype == "remote":
        return str(pd / "repo")
    if stype == "local":
        return manifest["source"]["local_path"]
    return str(pd)


def gen_preflight(manifest: dict, pd: Path) -> str:
    src        = manifest.get("source", {})
    build_cmds = manifest.get("build", {}).get("pre_start_scripts", [])
    stype      = source_type(manifest)
    lines      = ["#!/bin/bash", "set -e", ""]

    if stype == "remote":
        repo      = src["git_repo"]
        branch    = src.get("branch", "main")
        auto_pull = src.get("auto_pull", False)
        lines += [
            f'# ── remote git source ──',
            f'if [ ! -d "{pd}/repo/.git" ]; then',
            f'  echo "[sorc] Cloning {repo}"',
            f'  git clone --branch {branch} {repo} {pd}/repo',
            f'else',
        ]
        if auto_pull:
            lines += [
                f'  echo "[sorc] Pulling latest ({branch})"',
                f'  git -C {pd}/repo pull origin {branch}',
            ]
        else:
            lines += [f'  echo "[sorc] auto_pull disabled — skipping pull"']
        lines += ["fi", ""]

    elif stype == "local":
        local_path  = src["local_path"]
        auto_sync   = src.get("auto_sync", False)
        is_git_hint = src.get("is_git", True)   # hint: treat as git repo?
        lines += [
            f'# ── local path source ──',
            f'if [ ! -d "{local_path}" ]; then',
            f'  echo "[sorc] ERROR: local_path not found: {local_path}" >&2',
            f'  exit 1',
            f'fi',
            f'echo "[sorc] Using local path: {local_path}"',
            "",
        ]
        if auto_sync and is_git_hint:
            lines += [
                f'if [ -d "{local_path}/.git" ]; then',
                f'  echo "[sorc] auto_sync: pulling local git repo"',
                f'  git -C {local_path} pull',
                f'else',
                f'  echo "[sorc] auto_sync enabled but {local_path} is not a git repo — skipping"',
                f'fi',
                "",
            ]

    else:  # none
        lines += [
            "# ── no source configured — nothing to clone or sync ──",
            "",
        ]

    for cmd in build_cmds:
        safe = " ".join(str(c) for c in cmd)
        wd   = resolve_working_dir(manifest, pd)
        lines += [
            f'echo "[sorc] Build step: {safe}"',
            f'cd {wd}',
            safe,
            "",
        ]

    lines += ['echo "[sorc] Preflight complete."']
    return "\n".join(lines) + "\n"


def gen_launch(manifest: dict, pd: Path, backend) -> str:
    run_cfg     = manifest.get("run", {})
    cmd_list    = run_cfg.get("command", [])
    shell_cmd   = run_cfg.get("shell_command", "")
    working_dir = resolve_working_dir(manifest, pd)
    pod_name    = manifest["name"]

    if shell_cmd:
        exec_line = shell_cmd
    elif cmd_list:
        exec_line = " ".join(str(c) for c in cmd_list)
    else:
        exec_line = "echo '[sorc] No command configured.'"

    session = backend.session_name(pod_name)

    lines = [
        "#!/bin/bash",
        f'cd "{working_dir}"',
        f'[ -f "{pd}/.env" ] && set -a && source "{pd}/.env" && set +a',
        "",
        f'echo "[sorc] Working dir: {working_dir}"',
        f'echo "[sorc] Starting session: {session}"',
        f"{exec_line}",
    ]
    return "\n".join(lines) + "\n"


def gen_shutdown(manifest: dict, pd: Path, backend) -> str:
    pod_name = manifest["name"]
    lines = [
        "#!/bin/bash",
        f'echo "[sorc] Shutdown signal sent to {pod_name}"',
        "# systemd will send SIGINT after this script, then SIGKILL after TimeoutStopSec",
        "# Add any pre-shutdown tasks below:",
        "",
        "# Example: curl -s -X POST http://localhost:8000/shutdown || true",
    ]
    return "\n".join(lines) + "\n"


def gen_unit(manifest: dict, pd: Path) -> str:
    name = manifest["name"]
    lc   = manifest.get("lifecycle", {})
    user = pwd.getpwuid(os.getuid()).pw_name
    run_cfg = manifest.get("run", {})
    wd   = run_cfg.get("working_dir", str(pd))

    return f"""[Unit]
Description=Sorc Pod: {name}
After=network.target

StartLimitIntervalSec=3600
StartLimitBurst={lc.get("max_restarts_per_hour", 20)}

[Service]
Type=simple
User={user}
WorkingDirectory={wd}

EnvironmentFile={pd}/.env

ExecStartPre=/bin/bash {pd}/preflight.sh
ExecStart=/bin/bash {pd}/launch.sh
ExecStop=/bin/bash {pd}/shutdown.sh

KillSignal=SIGINT
TimeoutStopSec={lc.get("graceful_stop_sec", 30)}

Restart=always
RestartSec={lc.get("restart_sec", 10)}

MemoryMax={lc.get("max_memory", "1G")}
CPUQuota={lc.get("max_cpu_percent", 100)}%
CPUWeight={lc.get("cpu_weight", 50)}
TasksMax={lc.get("tasks_max", 512)}

[Install]
WantedBy=multi-user.target
"""

# ─────────────────────────────────────────────
# HEALTHCHECK
# ─────────────────────────────────────────────

def run_healthcheck(manifest: dict) -> tuple[bool, str]:
    hc = manifest.get("healthcheck", {})
    if not hc.get("enabled", False):
        return True, "disabled"

    htype   = hc.get("type", "http")
    timeout = hc.get("timeout_sec", 5)

    if htype == "http":
        url = hc.get("url", "")
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True, f"HTTP OK ({url})"
        except Exception as e:
            return False, f"HTTP FAIL ({url}): {e}"

    elif htype == "process_output":
        must_contain = hc.get("must_contain", "")
        name = manifest["name"]
        out = run_out(["journalctl", "-u", svc_name(name), "-n", "50", "--no-pager"])
        if must_contain in out:
            return True, f"Output contains '{must_contain}'"
        return False, f"Output missing '{must_contain}'"

    return False, f"Unknown healthcheck type: {htype}"

# ─────────────────────────────────────────────
# DEPENDENCY RESOLUTION
# ─────────────────────────────────────────────

def resolve_deps(name: str, visited=None, stack=None) -> list:
    if visited is None: visited = set()
    if stack   is None: stack   = []
    if name in stack:
        die(f"Circular dependency detected: {' → '.join(stack + [name])}", EXIT_DEP)
    if name in visited:
        return []
    stack.append(name)
    manifest = load_manifest(name)
    deps = manifest.get("depends_on", [])
    order = []
    for dep in deps:
        order += resolve_deps(dep, visited, stack)
    order.append(name)
    visited.add(name)
    stack.pop()
    return order

def wait_for_dep(dep_name: str, timeout=30) -> bool:
    import time
    dep_manifest = load_manifest(dep_name)
    hc = dep_manifest.get("healthcheck", {})
    has_hc = hc.get("enabled", False)
    deadline = time.time() + (60 if has_hc else timeout)

    info(f"Waiting for dependency '{dep_name}'...")
    while time.time() < deadline:
        if has_hc:
            ok_hc, _ = run_healthcheck(dep_manifest)
            if ok_hc: return True
        else:
            if is_active(dep_name): return True
        time.sleep(2)
    return False

# ─────────────────────────────────────────────
# COMMAND: create
# ─────────────────────────────────────────────

def cmd_create(args):
    name = args.name
    dry  = args.dry_run

    if pod_exists(name) and not args.force:
        die(f"Pod '{name}' already exists. Use --force to overwrite.", EXIT_MANIFEST)

    header(f"Creating pod: {name}")

    def ask(prompt, default=""):
        val = input(f"  {prompt} [{default}]: ").strip()
        return val if val else default

    # ── source type wizard ──
    src_type = ask("Source type  (remote / local / none)", "none")
    source   = {}

    if src_type == "remote":
        git_repo  = ask("Git repo URL", "https://github.com/user/repo.git")
        branch    = ask("Branch", "main")
        auto_pull = ask("Auto-pull on start?", "y").lower() == "y"
        source    = {"git_repo": git_repo, "branch": branch, "auto_pull": auto_pull}

    elif src_type == "local":
        local_path = ask("Local repo/directory path", f"/home/{os.getlogin()}/projects/{name}")
        auto_sync  = ask("Auto-sync (git pull) on start?", "n").lower() == "y"
        source     = {"local_path": local_path, "auto_sync": auto_sync, "is_git": True}
        # Validate path exists
        if not Path(local_path).exists():
            warn(f"Path '{local_path}' does not exist yet — make sure it exists before starting.")

    else:  # none
        info("No source — sorc will manage the process only.")

    # ── default working dir depends on source type ──
    if src_type == "remote":
        default_wd = f"~/.sorc/pods/{name}/repo"
    elif src_type == "local":
        default_wd = source.get("local_path", f"~/.sorc/pods/{name}")
    else:
        default_wd = f"~/.sorc/pods/{name}"

    command_str = ask("Run command", "python3 main.py")
    working_dir = ask("Working directory", default_wd)
    max_mem     = ask("Max memory", "1G")
    restart_sec = ask("Restart delay (sec)", "10")

    hc_enabled = ask("Enable healthcheck?", "n").lower() == "y"
    hc = {}
    if hc_enabled:
        hc_type = ask("Healthcheck type (http/process_output)", "http")
        hc = {"enabled": True, "type": hc_type, "interval_sec": 30,
              "timeout_sec": 5, "restart_on_fail": True}
        if hc_type == "http":
            hc["url"] = ask("Healthcheck URL", "http://localhost:8000/health")
        else:
            hc["must_contain"] = ask("Process output must contain", "READY")

    manifest = {
        "name": name,
        "meta": {
            "description": ask("Description", f"{name} agent"),
            "tags": [],
            "created_at": datetime.now().isoformat()
        },
        "source": source,
        "runtime": {"type": "process"},
        "terminal": {"backend": ask("Terminal backend (screen/tmux)", "screen")},
        "build": {"pre_start_scripts": []},
        "run": {
            "command": command_str.split(),
            "working_dir": working_dir,
            "shell_command": ""
        },
        "lifecycle": {
            "restart_sec": int(restart_sec),
            "graceful_stop_sec": 30,
            "max_memory": max_mem,
            "max_cpu_percent": 100,
            "cpu_weight": 50,
            "tasks_max": 512,
            "max_restarts_per_hour": 20
        },
        "depends_on": [],
        "healthcheck": hc if hc_enabled else {"enabled": False}
    }

    pd      = pod_dir(name)
    backend = get_backend(manifest)
    unit    = gen_unit(manifest, pd)
    pre     = gen_preflight(manifest, pd)
    launch  = gen_launch(manifest, pd, backend)
    shut    = gen_shutdown(manifest, pd, backend)

    if dry:
        header("─── DRY RUN — nothing written ───")
        print("\n[pod.json]\n" + json.dumps(manifest, indent=2))
        print("\n[preflight.sh]\n" + pre)
        print("\n[launch.sh]\n" + launch)
        print("\n[shutdown.sh]\n" + shut)
        print(f"\n[{unit_path(name)}]\n" + unit)
        return

    pd.mkdir(parents=True, exist_ok=True)
    save_manifest(name, manifest)
    write_script(pd / "preflight.sh", pre)
    write_script(pd / "launch.sh",    launch)
    write_script(pd / "shutdown.sh",  shut)

    env_file = pd / ".env"
    if not env_file.exists():
        env_file.write_text("# sorc environment variables\n# KEY=VALUE\n")

    gitignore = pd / ".gitignore"
    gitignore.write_text(".env\n")

    try:
        unit_path(name).write_text(unit)
        daemon_reload()
        ok(f"Systemd unit written: {unit_path(name)}")
    except PermissionError:
        warn(f"Could not write {unit_path(name)} — run as root or add sudoers entry.")
        warn(f"Unit file preview saved to {pd}/sorc.service")
        (pd / "sorc.service").write_text(unit)

    sorc_log(name, "create", {"source_type": src_type})
    ok(f"Pod '{name}' created at {pd}")
    info(f"Next: sorc start {name}")

# ─────────────────────────────────────────────
# COMMAND: start
# ─────────────────────────────────────────────

def cmd_start(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    header(f"Starting: {name}")

    # Resolve and start deps
    order = resolve_deps(name)
    for dep in order[:-1]:  # all except the pod itself
        if not is_active(dep):
            info(f"Starting dependency: {dep}")
            if not systemctl("start", dep):
                die(f"Failed to start dependency '{dep}'", EXIT_DEP)
            if not wait_for_dep(dep):
                die(f"Dependency '{dep}' did not become healthy in time.", EXIT_DEP)
            ok(f"Dependency '{dep}' is up.")

    if not systemctl("start", name):
        die(f"Failed to start '{name}'", EXIT_SYSTEMD)

    sorc_log(name, "start")
    ok(f"Pod '{name}' started.")

# ─────────────────────────────────────────────
# COMMAND: stop
# ─────────────────────────────────────────────

def cmd_stop(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)
    header(f"Stopping: {name}")
    if not systemctl("stop", name):
        die(f"Failed to stop '{name}'", EXIT_SYSTEMD)
    sorc_log(name, "stop")
    ok(f"Pod '{name}' stopped.")

# ─────────────────────────────────────────────
# COMMAND: restart
# ─────────────────────────────────────────────

def cmd_restart(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)
    header(f"Restarting: {name}")
    if not systemctl("restart", name):
        die(f"Failed to restart '{name}'", EXIT_SYSTEMD)
    sorc_log(name, "restart")
    ok(f"Pod '{name}' restarted.")

# ─────────────────────────────────────────────
# COMMAND: sync
# ─────────────────────────────────────────────

def cmd_sync(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)
    manifest = load_manifest(name)
    pd  = pod_dir(name)
    src = manifest.get("source", {})
    repo_dir = pd / "repo"

    header(f"Syncing: {name}")

    if src.get("git_repo") and repo_dir.exists():
        branch = src.get("branch", "main")
        info(f"Pulling {branch}...")
        run(["git", "-C", str(repo_dir), "pull", "origin", branch])
        ok("Git pull complete.")

    build_cmds = manifest.get("build", {}).get("pre_start_scripts", [])
    for cmd in build_cmds:
        info(f"Build: {' '.join(str(c) for c in cmd)}")
        run([str(c) for c in cmd])

    if not systemctl("restart", name):
        die(f"Failed to restart '{name}' after sync", EXIT_SYSTEMD)
    sorc_log(name, "sync")
    ok(f"Pod '{name}' synced and restarted.")

# ─────────────────────────────────────────────
# COMMAND: destroy
# ─────────────────────────────────────────────

def cmd_destroy(args):
    name = args.name
    dry  = args.dry_run
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    header(f"Destroying: {name}")
    pd = pod_dir(name)

    actions = [
        f"Stop service:       systemctl stop {svc_name(name)}",
        f"Disable service:    systemctl disable {svc_name(name)}",
        f"Remove unit:        {unit_path(name)}",
        f"Remove pod dir:     {pd}",
        f"Remove log:         {LOGS_DIR / (name + '.jsonl')}",
    ]
    for a in actions:
        print(f"  {a}")

    if dry:
        warn("Dry run — nothing destroyed.")
        return

    confirm = input("\n  Type pod name to confirm destruction: ").strip()
    if confirm != name:
        die("Confirmation mismatch. Aborted.")

    systemctl("stop",    name)
    systemctl("disable", name)
    try:
        unit_path(name).unlink(missing_ok=True)
        daemon_reload()
    except PermissionError:
        warn("Could not remove unit file — requires sudo.")

    shutil.rmtree(pd, ignore_errors=True)
    log_file = LOGS_DIR / f"{name}.jsonl"
    log_file.unlink(missing_ok=True)

    ok(f"Pod '{name}' destroyed.")

# ─────────────────────────────────────────────
# COMMAND: status
# ─────────────────────────────────────────────

def pod_state(name: str) -> str:
    if not pod_exists(name):       return "not_found"
    if not unit_path(name).exists(): return "created"
    active = run_out(["systemctl", "is-active", svc_name(name)])
    if active == "active":         return "running"
    if active == "failed":         return "failed"
    if active == "inactive":       return "stopped"
    return "configured"

def cmd_status(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    manifest = load_manifest(name)
    header(f"Status: {name}")

    state = pod_state(name)
    state_color = {
        "running":    "\033[92m",
        "stopped":    "\033[93m",
        "failed":     "\033[91m",
        "created":    "\033[94m",
        "configured": "\033[94m",
    }.get(state, "")
    print(f"  State:    {state_color}{state}\033[0m")

    # systemctl show fields
    fields = ["MainPID", "ActiveEnterTimestamp", "NRestarts", "MemoryCurrent", "CPUUsageNSec"]
    props = {}
    for f in fields:
        val = run_out(["systemctl", "show", svc_name(name), f"--property={f}"])
        if "=" in val:
            props[f] = val.split("=", 1)[1]

    pid = props.get("MainPID", "—")
    mem = props.get("MemoryCurrent", "—")
    started = props.get("ActiveEnterTimestamp", "—")
    restarts = props.get("NRestarts", "0")

    print(f"  PID:      {pid}")
    print(f"  Memory:   {mem}")
    print(f"  Started:  {started}")
    print(f"  Restarts: {restarts}")

    # CPU from ps if running
    if pid and pid not in ("—", "0"):
        cpu = run_out(["ps", "-p", pid, "-o", "%cpu="])
        if cpu:
            print(f"  CPU%:     {cpu}")

    # Healthcheck
    hc_ok, hc_msg = run_healthcheck(manifest)
    hc_str = f"\033[92m{hc_msg}\033[0m" if hc_ok else f"\033[91m{hc_msg}\033[0m"
    print(f"  Health:   {hc_str}")

# ─────────────────────────────────────────────
# COMMAND: list
# ─────────────────────────────────────────────

def cmd_list(args):
    header("Pods")
    if not PODS_DIR.exists() or not any(PODS_DIR.iterdir()):
        info("No pods found. Run: sorc create <name>")
        return

    rows = []
    for pd in sorted(PODS_DIR.iterdir()):
        if not (pd / "pod.json").exists():
            continue
        name = pd.name
        state = pod_state(name)
        state_sym = {"running": "●", "stopped": "○", "failed": "✗",
                     "created": "◌", "configured": "◌"}.get(state, "?")

        pid = run_out(["systemctl", "show", svc_name(name), "--property=MainPID"]).replace("MainPID=", "")
        cpu = run_out(["ps", "-p", pid, "-o", "%cpu="]) if pid not in ("", "0") else "—"
        mem = run_out(["ps", "-p", pid, "-o", "%mem="]) if pid not in ("", "0") else "—"

        started = run_out(["systemctl", "show", svc_name(name),
                           "--property=ActiveEnterTimestamp"]).replace("ActiveEnterTimestamp=", "")
        started = started[:16] if started else "—"

        rows.append([f"{state_sym} {name}", state, cpu or "—", mem or "—", started])

    if rows:
        table(rows, ["NAME", "STATE", "CPU%", "MEM%", "STARTED"])
    else:
        info("No configured pods found.")

# ─────────────────────────────────────────────
# COMMAND: logs
# ─────────────────────────────────────────────

def cmd_logs(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)
    cmd = ["journalctl", "-u", svc_name(name), "--no-pager"]
    if args.follow:
        cmd.append("-f")
    os.execvp("journalctl", cmd)  # replace process — clean Ctrl+C handling

# ─────────────────────────────────────────────
# COMMAND: exec
# ─────────────────────────────────────────────

def cmd_exec(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    manifest = load_manifest(name)
    backend  = get_backend(manifest)

    if not backend.session_exists(name):
        die(f"No active terminal session for '{name}'. Is the pod running?", EXIT_NOT_FOUND)

    print(f"\n  \033[93m{backend.detach_hint()}\033[0m\n")
    attach = backend.attach_cmd(name)
    os.execvp(attach[0], attach)

# ─────────────────────────────────────────────
# COMMAND: config
# ─────────────────────────────────────────────

def cmd_config(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    if "=" not in args.keyvalue:
        die("Expected KEY=VALUE format.", EXIT_MANIFEST)

    key, value = args.keyvalue.split("=", 1)
    key = key.strip()
    env_file = pod_dir(name) / ".env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []

    found = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}")

    env_file.write_text("\n".join(new_lines) + "\n")
    ok(f"Set {key}={value} in {env_file}")

# ─────────────────────────────────────────────
# COMMAND: doctor
# ─────────────────────────────────────────────

def cmd_doctor(args):
    header("sorc doctor")
    checks = []

    def check(label, passed, hint=""):
        sym  = "\033[92m✓\033[0m" if passed else "\033[91m✗\033[0m"
        line = f"  {sym}  {label}"
        if not passed and hint:
            line += f"\n       → {hint}"
        checks.append((passed, line))
        print(line)

    # systemd
    r = subprocess.run(["systemctl", "--version"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check("systemd available", r.returncode == 0, "Install systemd")

    # journalctl
    check("journalctl available", which("journalctl"), "Part of systemd")

    # screen / tmux
    check("screen installed", which("screen"), "sudo apt install screen")
    check("tmux installed",   which("tmux"),   "sudo apt install tmux")

    # git
    check("git installed", which("git"), "sudo apt install git")

    # write perms
    try:
        test = SORC_DIR / ".write_test"
        test.touch(); test.unlink()
        check("~/.sorc writable", True)
    except Exception:
        check("~/.sorc writable", False, f"chmod u+w {SORC_DIR}")

    # cgroup v2
    cg2 = Path("/sys/fs/cgroup/cgroup.controllers").exists()
    check("cgroup v2 enabled", cg2, "Enable unified cgroup hierarchy in kernel params")

    # disk space (warn if < 1GB)
    try:
        st = os.statvfs(str(SORC_DIR))
        free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        check(f"Disk space ({free_gb:.1f}GB free)", free_gb > 1.0,
              "Less than 1GB free — pods may fail to clone/build")
    except Exception:
        check("Disk space", False, "Could not stat filesystem")

    # port conflicts via ss
    check("ss available for port scanning", which("ss"), "sudo apt install iproute2")

    passed = sum(1 for ok_flag, _ in checks if ok_flag)
    total  = len(checks)
    print(f"\n  {passed}/{total} checks passed.")
    if passed < total:
        sys.exit(1)

# ─────────────────────────────────────────────
# COMMAND: snapshot
# ─────────────────────────────────────────────

def cmd_snapshot(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    ts  = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = SNAP_DIR / name / ts
    dst.mkdir(parents=True, exist_ok=True)
    pd  = pod_dir(name)

    header(f"Snapshot: {name} → {dst}")

    # manifest
    shutil.copy2(pod_manifest(name), dst / "pod.json")
    ok("pod.json")

    # .env (sensitive — warn)
    env_src = pd / ".env"
    if env_src.exists():
        shutil.copy2(env_src, dst / ".env")
        warn(".env copied — treat as sensitive, do not share snapshot publicly.")

    # git SHA
    repo_dir = pd / "repo"
    if repo_dir.exists():
        sha = run_out(["git", "-C", str(repo_dir), "rev-parse", "HEAD"])
        (dst / "git_sha.txt").write_text(sha + "\n")
        ok(f"Git SHA: {sha[:12]}")

    # recent logs
    logs_out = run_out(["journalctl", "-u", svc_name(name), "-n", "500", "--no-pager"])
    (dst / "journal.log").write_text(logs_out)
    ok("Last 500 journal lines captured.")

    # metadata
    meta = {
        "timestamp": ts,
        "pod": name,
        "state": pod_state(name),
        "pid":   run_out(["systemctl", "show", svc_name(name), "--property=MainPID"])
                         .replace("MainPID=", ""),
        "restarts": run_out(["systemctl", "show", svc_name(name), "--property=NRestarts"])
                            .replace("NRestarts=", ""),
    }
    (dst / "meta.json").write_text(json.dumps(meta, indent=2))
    sorc_log(name, "snapshot", {"path": str(dst)})
    ok(f"Snapshot complete: {dst}")

# ─────────────────────────────────────────────
# COMMAND: healthcheck
# ─────────────────────────────────────────────

def cmd_healthcheck(args):
    name = args.name
    if not pod_exists(name):
        die(f"Pod '{name}' not found.", EXIT_NOT_FOUND)

    manifest = load_manifest(name)
    header(f"Healthcheck: {name}")

    passed, msg = run_healthcheck(manifest)
    if passed:
        ok(msg)
    else:
        err(msg)
        sys.exit(EXIT_HEALTH)

# ─────────────────────────────────────────────
# MAIN / ARGPARSE
# ─────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sorc",
        description="sorc — Screen Orchestrator. Manage local self-healing agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"sorc {VERSION}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # create
    c = sub.add_parser("create", help="Create a new pod (interactive wizard)")
    c.add_argument("name")
    c.add_argument("--dry-run", action="store_true", help="Preview without writing")
    c.add_argument("--force",   action="store_true", help="Overwrite existing pod")

    # start / stop / restart
    for cmd in ("start", "stop", "restart"):
        s = sub.add_parser(cmd, help=f"{cmd.capitalize()} a pod")
        s.add_argument("name")

    # sync
    s = sub.add_parser("sync", help="Pull + rebuild + restart a pod")
    s.add_argument("name")

    # destroy
    s = sub.add_parser("destroy", help="Remove a pod and its systemd unit")
    s.add_argument("name")
    s.add_argument("--dry-run", action="store_true")

    # status
    s = sub.add_parser("status", help="Show pod status")
    s.add_argument("name")

    # list
    sub.add_parser("list", help="List all pods")

    # logs
    s = sub.add_parser("logs", help="Stream pod logs")
    s.add_argument("name")
    s.add_argument("--follow", "-f", action="store_true")

    # exec
    s = sub.add_parser("exec", help="Attach to pod terminal session")
    s.add_argument("name")

    # config
    s = sub.add_parser("config", help="Set an env variable (KEY=VALUE)")
    s.add_argument("name")
    s.add_argument("keyvalue", metavar="KEY=VALUE")

    # doctor
    sub.add_parser("doctor", help="Check system prerequisites")

    # snapshot
    s = sub.add_parser("snapshot", help="Snapshot pod state and logs")
    s.add_argument("name")

    # healthcheck
    s = sub.add_parser("healthcheck", help="Manually run pod healthcheck")
    s.add_argument("name")

    return p


COMMANDS = {
    "create":      cmd_create,
    "start":       cmd_start,
    "stop":        cmd_stop,
    "restart":     cmd_restart,
    "sync":        cmd_sync,
    "destroy":     cmd_destroy,
    "status":      cmd_status,
    "list":        cmd_list,
    "logs":        cmd_logs,
    "exec":        cmd_exec,
    "config":      cmd_config,
    "doctor":      cmd_doctor,
    "snapshot":    cmd_snapshot,
    "healthcheck": cmd_healthcheck,
}


def main():
    ensure_dirs()
    parser = build_parser()
    args   = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = COMMANDS.get(args.command)
    if not handler:
        die(f"Unknown command: {args.command}")

    try:
        handler(args)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
