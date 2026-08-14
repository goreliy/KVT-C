import argparse
import json
import os
import runpy
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from shared.paths import app_root, logs_dir, run_dir, seed_default_configs


ROOT = Path(app_root())
LOG_DIR = Path(logs_dir())
PID_DIR = Path(run_dir())
SYSTEM_CONFIG = ROOT / "data" / "config" / "system_config.json"
OPCUA_CONFIG = ROOT / "data" / "config" / "opcua_config.json"
MQTT_CONFIG = ROOT / "data" / "config" / "mqtt_config.json"

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
    "mqtt": {
        "module": "mqtt_bridge.app",
        "port": 1883,
        "host": "0.0.0.0",
        "config_file": "mqtt",
        "pass_bind_args": False,
        "pid_file": PID_DIR / "mqtt.pid",
        "stdout": LOG_DIR / "mqtt.out.log",
        "stderr": LOG_DIR / "mqtt.err.log",
    },
}


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)


def _bootstrap_runtime() -> None:
    _ensure_dirs()
    seed_default_configs()


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _run_internal_service(module: str, argv: list[str]) -> None:
    _bootstrap_runtime()
    sys.argv = [module, *argv]
    runpy.run_module(module, run_name="__main__")


def _service_command(module: str) -> list[str]:
    if _is_frozen():
        return [sys.executable, "--internal-service", module]
    return [sys.executable, "-m", module]


def _is_windows() -> bool:
    return os.name == "nt"


def _network_config() -> dict:
    try:
        with open(SYSTEM_CONFIG, "r", encoding="utf-8-sig") as handle:
            return (json.load(handle).get("network") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _load_config(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _opcua_config() -> dict:
    return _load_config(OPCUA_CONFIG)


def _opcua_server_config() -> dict:
    return _opcua_config().get("server") or {}


def _mqtt_config() -> dict:
    return _load_config(MQTT_CONFIG)


def _mqtt_broker_config() -> dict:
    return _mqtt_config().get("broker") or {}


def _service_bind(name: str) -> tuple[str, int]:
    cfg = SERVICES[name]
    if cfg.get("config_file") == "opcua":
        server = _opcua_server_config()
        host = str(server.get("host", cfg["host"]) or cfg["host"]).strip()
        port = int(server.get("port", cfg["port"]) or cfg["port"])
        return host, port
    if cfg.get("config_file") == "mqtt":
        broker = _mqtt_broker_config()
        host = str(broker.get("host", cfg["host"]) or cfg["host"]).strip()
        port = int(broker.get("port", cfg["port"]) or cfg["port"])
        return host, port
    network = _network_config()
    host = str(network.get(cfg["network_host_key"], cfg["host"]) or cfg["host"]).strip()
    port = int(network.get(cfg["network_port_key"], cfg["port"]) or cfg["port"])
    return host, port


def _as_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да"}
    return bool(value)


def _service_config(name: str) -> dict:
    cfg = SERVICES[name]
    if cfg.get("config_file") == "opcua":
        return _opcua_config()
    if cfg.get("config_file") == "mqtt":
        return _mqtt_config()
    return {}


def _service_autostart(name: str) -> bool:
    config = _service_config(name)
    if not config:
        return True
    if "autostart" in config:
        return _as_bool(config.get("autostart"), default=True)
    if "enabled" in config:
        return _as_bool(config.get("enabled"), default=True)
    return True


def _autostart_label(name: str) -> str:
    if not _service_config(name):
        return ""
    return f", autostart={'on' if _service_autostart(name) else 'off'}"


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


def start_service(name: str, honor_autostart: bool = False) -> None:
    cfg = SERVICES[name]
    host, port = _service_bind(name)
    if honor_autostart and not _service_autostart(name):
        print(f"{name}: skipped by autostart setting (host={host}, port={port})")
        return
    pid = _read_pid(cfg["pid_file"])
    if pid and _pid_alive(pid):
        print(f"{name}: already running (pid={pid}, host={host}, port={port}{_autostart_label(name)})")
        return
    if pid and not _pid_alive(pid):
        _remove_pid(cfg["pid_file"])

    cmd = _service_command(cfg["module"])
    if cfg.get("pass_bind_args", True):
        cmd.extend(["--host", host, "--port", str(port)])

    creationflags = 0
    if _is_windows():
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    with open(cfg["stdout"], "a", encoding="utf-8") as stdout, open(cfg["stderr"], "a", encoding="utf-8") as stderr:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=stdout, stderr=stderr, creationflags=creationflags)
    _write_pid(cfg["pid_file"], proc.pid)
    print(f"{name}: started (pid={proc.pid}, host={host}, port={port}{_autostart_label(name)})")


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


def status_services(targets=None) -> None:
    for name in (targets or SERVICES.keys()):
        cfg = SERVICES[name]
        host, port = _service_bind(name)
        pid = _read_pid(cfg["pid_file"])
        if pid and _pid_alive(pid):
            print(f"{name}: running (pid={pid}, host={host}, port={port}{_autostart_label(name)})")
        elif pid:
            print(f"{name}: stale pid={pid}{_autostart_label(name)}")
        else:
            print(f"{name}: stopped (host={host}, port={port}{_autostart_label(name)})")


def _browser_url() -> str:
    host, port = _service_bind("visualizer")
    if host in {"", "0.0.0.0", "::", "localhost"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in {"", "0.0.0.0", "::", "localhost"} else host.strip("[]")


def _wait_for_service_port(name: str, timeout_s: float = 60.0) -> bool:
    host, port = _service_bind(name)
    host = _connect_host(host)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _wait_for_visualizer(url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if 200 <= response.status < 500:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def desktop_start() -> int:
    _bootstrap_runtime()
    print("KVT-C: starting Visualizer...")
    start_service("visualizer", honor_autostart=True)

    url = _browser_url()
    _wait_for_service_port("visualizer", timeout_s=120.0)

    print("KVT-C: starting background services...")
    for name in ["poller", "archiver", "opcua", "mqtt"]:
        start_service(name, honor_autostart=True)
        if name != "mqtt":
            _wait_for_service_port(name, timeout_s=60.0)

    print(f"KVT-C: opening visualizer {url}")
    if _wait_for_visualizer(url, timeout_s=120.0):
        print("KVT-C: visualizer is ready.")
    else:
        print("KVT-C: visualizer may still be warming up; refresh the page in a few seconds if needed.")
    webbrowser.open(url)

    print("")
    status_services(selected_services("all"))
    print("")
    print("This window can be closed; services keep running in background.")
    print("To stop: run KVT-C.exe stop")
    try:
        input("Press Enter to close this window...")
    except EOFError:
        pass
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="KVT single entrypoint launcher")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument("--service", choices=["all", "poller", "archiver", "visualizer", "opcua", "mqtt"], default="all")
    return parser.parse_args()


def selected_services(service: str):
    return ["poller", "archiver", "visualizer", "opcua", "mqtt"] if service == "all" else [service]


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--internal-service":
        _run_internal_service(sys.argv[2], sys.argv[3:])
        return 0
    if len(sys.argv) == 1 and _is_frozen():
        return desktop_start()

    _bootstrap_runtime()
    args = parse_args()
    targets = selected_services(args.service)

    if args.command == "start":
        for name in targets:
            start_service(name, honor_autostart=args.service == "all")
            if _is_frozen() and name != "mqtt":
                _wait_for_service_port(name, timeout_s=60.0)
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
            start_service(name, honor_autostart=args.service == "all")
            if _is_frozen() and name != "mqtt":
                _wait_for_service_port(name, timeout_s=60.0)
    else:
        status_services(targets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

