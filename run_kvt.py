import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PID_DIR = ROOT / ".run"
SYSTEM_CONFIG = ROOT / "data" / "config" / "system_config.json"
OPCUA_CONFIG = ROOT / "data" / "config" / "opcua_config.json"

SERVICES = {
    "poller": {
        "module": "poller.app",
        "port": 5001,
        "host": "0.0.0.0",
        "network_host_key": "poller_host",
        "network_port_key": "poller_port",
        "pid_file": PID_DIR / "poller.pid",
        "stdout": LOG_DIR / "poller.out.log",
        "stderr": LOG_DIR / "poller.err.log",
    },
    "archiver": {
        "module": "archiver.app",
        "port": 5002,
        "host": "0.0.0.0",
        "network_host_key": "archiver_host",
        "network_port_key": "archiver_port",
        "pid_file": PID_DIR / "archiver.pid",
        "stdout": LOG_DIR / "archiver.out.log",
        "stderr": LOG_DIR / "archiver.err.log",
    },
    "visualizer": {
        "module": "visualizer.app",
        "port": 5000,
        "host": "0.0.0.0",
        "network_host_key": "web_host",
        "network_port_key": "web_port",
        "pid_file": PID_DIR / "visualizer.pid",
        "stdout": LOG_DIR / "visualizer.out.log",
        "stderr": LOG_DIR / "visualizer.err.log",
    },
    "opcua": {
        "module": "opcua_server.app",
        "port": 4840,
        "host": "0.0.0.0",
        "config_file": "opcua",
        "pass_bind_args": False,
        "pid_file": PID_DIR / "opcua.pid",
        "stdout": LOG_DIR / "opcua.out.log",
        "stderr": LOG_DIR / "opcua.err.log",
    },
}


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _is_windows() -> bool:
    return os.name == "nt"


def _network_config() -> dict:
    try:
        with open(SYSTEM_CONFIG, "r", encoding="utf-8-sig") as handle:
            return (json.load(handle).get("network") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _opcua_server_config() -> dict:
    try:
        with open(OPCUA_CONFIG, "r", encoding="utf-8-sig") as handle:
            return (json.load(handle).get("server") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _service_bind(name: str) -> tuple[str, int]:
    cfg = SERVICES[name]
    if cfg.get("config_file") == "opcua":
        server = _opcua_server_config()
        host = str(server.get("host", cfg["host"]) or cfg["host"]).strip()
        port = int(server.get("port", cfg["port"]) or cfg["port"])
        return host, port
    network = _network_config()
    host = str(network.get(cfg["network_host_key"], cfg["host"]) or cfg["host"]).strip()
    port = int(network.get(cfg["network_port_key"], cfg["port"]) or cfg["port"])
    return host, port


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
    host, port = _service_bind(name)
    pid = _read_pid(cfg["pid_file"])
    if pid and _pid_alive(pid):
        print(f"{name}: already running (pid={pid}, host={host}, port={port})")
        return
    if pid and not _pid_alive(pid):
        _remove_pid(cfg["pid_file"])

    cmd = [sys.executable, "-m", cfg["module"]]
    if cfg.get("pass_bind_args", True):
        cmd.extend(["--host", host, "--port", str(port)])

    creationflags = 0
    if _is_windows():
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with open(cfg["stdout"], "a", encoding="utf-8") as stdout, open(cfg["stderr"], "a", encoding="utf-8") as stderr:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=stdout, stderr=stderr, creationflags=creationflags)
    _write_pid(cfg["pid_file"], proc.pid)
    print(f"{name}: started (pid={proc.pid}, host={host}, port={port})")


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
        host, port = _service_bind(name)
        pid = _read_pid(cfg["pid_file"])
        if pid and _pid_alive(pid):
            print(f"{name}: running (pid={pid}, host={host}, port={port})")
        elif pid:
            print(f"{name}: stale pid={pid}")
        else:
            print(f"{name}: stopped (host={host}, port={port})")


def parse_args():
    parser = argparse.ArgumentParser(description="KVT single entrypoint launcher")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--service", choices=["all", "poller", "archiver", "visualizer", "opcua"], default="all")
    return parser.parse_args()


def selected_services(service: str):
    return ["poller", "archiver", "visualizer", "opcua"] if service == "all" else [service]


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
        # A tiny pause avoids Windows reporting recently killed sockets as still bound.
        time.sleep(0.5)
    elif args.command == "restart":
        for name in reversed(targets):
            stop_service(name)
        time.sleep(0.5)
        for name in targets:
            start_service(name)
    else:
        status_services()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
