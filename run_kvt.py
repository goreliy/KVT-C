import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PID_DIR = ROOT / ".run"

SERVICES = {
    "poller": {
        "module": "poller.app",
        "port": 5001,
        "host": "127.0.0.1",
        "pid_file": PID_DIR / "poller.pid",
        "stdout": LOG_DIR / "poller.out.log",
        "stderr": LOG_DIR / "poller.err.log",
    },
    "visualizer": {
        "module": "visualizer.app",
        "port": 5000,
        "host": "127.0.0.1",
        "pid_file": PID_DIR / "visualizer.pid",
        "stdout": LOG_DIR / "visualizer.out.log",
        "stderr": LOG_DIR / "visualizer.err.log",
    },
}


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _is_windows() -> bool:
    return os.name == "nt"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _is_windows():
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
        )
        out = (proc.stdout or "").strip()
        if not out:
            return False
        low = out.lower()
        if low.startswith("info:") or low.startswith("информация:"):
            return False
        return proc.returncode == 0 and out.startswith("\"")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path):
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")


def _remove_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def start_service(name: str) -> None:
    cfg = SERVICES[name]
    pid = _read_pid(cfg["pid_file"])
    if pid and _pid_alive(pid):
        print(f"{name}: already running (pid={pid})")
        return
    if pid and not _pid_alive(pid):
        _remove_pid(cfg["pid_file"])

    cmd = [sys.executable, "-m", cfg["module"], "--host", cfg["host"], "--port", str(cfg["port"])]

    creationflags = 0
    if _is_windows():
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with open(cfg["stdout"], "a", encoding="utf-8") as stdout, open(cfg["stderr"], "a", encoding="utf-8") as stderr:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=stdout, stderr=stderr, creationflags=creationflags)
    _write_pid(cfg["pid_file"], proc.pid)
    print(f"{name}: started (pid={proc.pid}, port={cfg['port']})")


def stop_service(name: str) -> None:
    cfg = SERVICES[name]
    pid = _read_pid(cfg["pid_file"])
    if not pid:
        print(f"{name}: not running (no pid file)")
        return
    if not _pid_alive(pid):
        print(f"{name}: stale pid file removed")
        _remove_pid(cfg["pid_file"])
        return

    if _is_windows():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError as exc:
            print(f"{name}: stop failed: {exc}")
            return

    time.sleep(0.3)
    if _pid_alive(pid):
        print(f"{name}: stop requested but process still alive (pid={pid})")
    else:
        print(f"{name}: stopped")
        _remove_pid(cfg["pid_file"])


def status_services() -> None:
    for name, cfg in SERVICES.items():
        pid = _read_pid(cfg["pid_file"])
        if pid and _pid_alive(pid):
            print(f"{name}: running (pid={pid}, port={cfg['port']})")
        elif pid:
            print(f"{name}: stale pid={pid}")
        else:
            print(f"{name}: stopped")


def parse_args():
    parser = argparse.ArgumentParser(description="KVT single entrypoint launcher")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--service", choices=["all", "poller", "visualizer"], default="all")
    return parser.parse_args()


def selected_services(service: str):
    return ["poller", "visualizer"] if service == "all" else [service]


def main() -> int:
    _ensure_dirs()
    args = parse_args()
    targets = selected_services(args.service)

    if args.command == "start":
        for name in targets:
            start_service(name)
    elif args.command == "stop":
        for name in reversed(targets):
            stop_service(name)
    elif args.command == "restart":
        for name in reversed(targets):
            stop_service(name)
        for name in targets:
            start_service(name)
    else:
        status_services()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
