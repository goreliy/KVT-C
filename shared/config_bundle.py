import io
import json
import os
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from shared.config_manager import default_mqtt_config, load_json, save_json


BUNDLE_FORMAT = "kvt-config-bundle"
BUNDLE_VERSION = 1
MAX_BUNDLE_SIZE = 100 * 1024 * 1024
MAX_MEMBER_SIZE = 25 * 1024 * 1024
ALLOWED_FLOORPLAN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
REQUIRED_CONFIG_FILES = {
    "system_config.json",
    "poller_config.json",
    "opcua_config.json",
    "archive_config.json",
    "notifications.json",
    "theme_config.json",
    "layout.json",
    "floorplan_config.json",
    "mnemo_tree.json",
}
DIAGNOSTIC_DATA_FILES = (
    "current.json",
    "availability_daily.json",
    "modbus_log.json",
    "events.json",
    "opcua_status.json",
    "mqtt_status.json",
    "mqtt_inbound.json",
)
OPTIONAL_DEFAULT_CONFIG_FILES = {
    "mqtt_config.json": default_mqtt_config,
}
ALLOWED_DIRECTORY_ENTRIES = {"config/", "assets/", "assets/floorplans/", "diagnostics/"}


class ConfigBundleError(ValueError):
    pass


def project_root() -> str:
    from shared.paths import app_root
    return app_root()


def _config_dir(root_dir: str) -> str:
    return os.path.join(root_dir, "data", "config")


def _data_dir(root_dir: str) -> str:
    return os.path.join(root_dir, "data")


def _floorplan_dir(root_dir: str) -> str:
    return os.path.join(root_dir, "visualizer", "static", "floorplans")


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _bundle_filename() -> str:
    return f"kvt-config-bundle-{_now_stamp()}.zip"


def _safe_zip_name(name: str) -> str:
    normalized = str(name or "").replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or any(part in ("", ".", "..") for part in normalized.split("/"))
    ):
        raise ConfigBundleError(f"Недопустимый путь в архиве: {name}")
    return normalized


def _safe_zip_dir_name(name: str) -> str:
    normalized = str(name or "").replace("\\", "/")
    stripped = normalized.strip("/")
    if (
        not stripped
        or normalized.startswith("/")
        or ":" in normalized
        or any(part in ("", ".", "..") for part in stripped.split("/"))
    ):
        raise ConfigBundleError(f"Недопустимый путь в архиве: {name}")
    return stripped + "/"


def _iter_config_files(root_dir: str) -> List[Tuple[str, str]]:
    config_dir = _config_dir(root_dir)
    if not os.path.isdir(config_dir):
        return []
    result = []
    for name in sorted(os.listdir(config_dir)):
        path = os.path.join(config_dir, name)
        if os.path.isfile(path) and name.lower().endswith(".json"):
            result.append((name, path))
    return result


def _iter_floorplan_assets(root_dir: str) -> List[Tuple[str, str]]:
    floorplan_dir = _floorplan_dir(root_dir)
    if not os.path.isdir(floorplan_dir):
        return []
    result = []
    for name in sorted(os.listdir(floorplan_dir)):
        path = os.path.join(floorplan_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if os.path.isfile(path) and ext in ALLOWED_FLOORPLAN_EXTENSIONS:
            result.append((name, path))
    return result


def _diagnostic_files(root_dir: str) -> List[Tuple[str, str]]:
    data_dir = _data_dir(root_dir)
    result = []
    for name in DIAGNOSTIC_DATA_FILES:
        path = os.path.join(data_dir, name)
        if os.path.isfile(path):
            result.append((name, path))
    return result


def export_config_bundle(root_dir: Optional[str] = None, include_diagnostics: bool = True) -> Tuple[bytes, str, Dict[str, Any]]:
    root_dir = root_dir or project_root()
    config_files = _iter_config_files(root_dir)
    floorplan_assets = _iter_floorplan_assets(root_dir)
    diagnostics = _diagnostic_files(root_dir) if include_diagnostics else []
    created_at = datetime.now().isoformat()
    manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "created_at": created_at,
        "source": {
            "app": "KVT-C",
        },
        "restore_scope": {
            "config": True,
            "floorplan_assets": True,
            "diagnostics": False,
        },
        "config_files": [name for name, _ in config_files],
        "floorplan_assets": [name for name, _ in floorplan_assets],
        "diagnostics": [name for name, _ in diagnostics],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, path in config_files:
            archive.write(path, f"config/{name}")
        for name, path in floorplan_assets:
            archive.write(path, f"assets/floorplans/{name}")
        for name, path in diagnostics:
            archive.write(path, f"diagnostics/{name}")
    return buffer.getvalue(), _bundle_filename(), manifest


def _read_manifest(archive: zipfile.ZipFile) -> Dict[str, Any]:
    try:
        with archive.open("manifest.json") as handle:
            manifest = json.loads(handle.read().decode("utf-8-sig"))
    except KeyError as exc:
        raise ConfigBundleError("В архиве нет manifest.json") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigBundleError("manifest.json повреждён или не является JSON") from exc
    if manifest.get("format") != BUNDLE_FORMAT:
        raise ConfigBundleError("Архив не является конфигурационным архивом КВТ")
    if int(manifest.get("version", 0) or 0) > BUNDLE_VERSION:
        raise ConfigBundleError("Архив создан более новой версией системы")
    return manifest


def _validate_members(archive: zipfile.ZipFile) -> None:
    total_size = 0
    for info in archive.infolist():
        if info.is_dir():
            dirname = _safe_zip_dir_name(info.filename)
            if dirname not in ALLOWED_DIRECTORY_ENTRIES:
                raise ConfigBundleError(f"Неизвестная директория в архиве: {dirname}")
            continue
        name = _safe_zip_name(info.filename)
        total_size += info.file_size
        if total_size > MAX_BUNDLE_SIZE:
            raise ConfigBundleError("Распакованный архив слишком большой")
        if info.file_size > MAX_MEMBER_SIZE:
            raise ConfigBundleError(f"Файл в архиве слишком большой: {name}")
        if name == "manifest.json":
            continue
        if name.startswith("config/"):
            rest = name[len("config/"):]
            if "/" in rest or not rest.lower().endswith(".json"):
                raise ConfigBundleError(f"Недопустимый конфигурационный файл: {name}")
            continue
        if name.startswith("assets/floorplans/"):
            rest = name[len("assets/floorplans/"):]
            ext = os.path.splitext(rest)[1].lower()
            if "/" in rest or ext not in ALLOWED_FLOORPLAN_EXTENSIONS:
                raise ConfigBundleError(f"Недопустимый файл плана помещения: {name}")
            continue
        if name.startswith("diagnostics/"):
            rest = name[len("diagnostics/"):]
            if "/" in rest:
                raise ConfigBundleError(f"Недопустимый диагностический файл: {name}")
            continue
        raise ConfigBundleError(f"Неизвестный путь в архиве: {name}")


def _read_config_payloads(archive: zipfile.ZipFile) -> Dict[str, Any]:
    configs: Dict[str, Any] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = _safe_zip_name(info.filename)
        if not name.startswith("config/"):
            continue
        filename = name[len("config/"):]
        if filename in configs:
            raise ConfigBundleError(f"Дублирующийся конфигурационный файл: {filename}")
        try:
            with archive.open(info) as handle:
                configs[filename] = json.loads(handle.read().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigBundleError(f"{filename} повреждён или не является JSON") from exc
    missing = sorted(REQUIRED_CONFIG_FILES - set(configs))
    if missing:
        raise ConfigBundleError("В архиве нет обязательных конфигов: " + ", ".join(missing))
    for filename, factory in OPTIONAL_DEFAULT_CONFIG_FILES.items():
        configs.setdefault(filename, factory())
    return configs


def _read_floorplan_assets(archive: zipfile.ZipFile) -> Dict[str, bytes]:
    assets: Dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = _safe_zip_name(info.filename)
        if not name.startswith("assets/floorplans/"):
            continue
        filename = name[len("assets/floorplans/"):]
        if filename in assets:
            raise ConfigBundleError(f"Дублирующийся файл плана помещения: {filename}")
        with archive.open(info) as handle:
            assets[filename] = handle.read()
    return assets


def _write_pre_import_backup(root_dir: str) -> str:
    backup_bytes, backup_name, _ = export_config_bundle(root_dir=root_dir, include_diagnostics=True)
    backup_dir = os.path.join(_config_dir(root_dir), "import_backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_name = backup_name.replace("kvt-config-bundle-", "kvt-config-before-import-")
    backup_path = os.path.join(backup_dir, backup_name)
    with open(backup_path, "wb") as handle:
        handle.write(backup_bytes)
    return backup_path


def import_config_bundle(bundle_bytes: bytes, root_dir: Optional[str] = None, create_backup: bool = True) -> Dict[str, Any]:
    root_dir = root_dir or project_root()
    if not bundle_bytes:
        raise ConfigBundleError("Файл архива пуст")
    if len(bundle_bytes) > MAX_BUNDLE_SIZE:
        raise ConfigBundleError("Архив слишком большой")

    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise ConfigBundleError("Файл не является ZIP-архивом") from exc

    with archive:
        manifest = _read_manifest(archive)
        _validate_members(archive)
        configs = _read_config_payloads(archive)
        assets = _read_floorplan_assets(archive)

        backup_path = _write_pre_import_backup(root_dir) if create_backup else None

        config_dir = _config_dir(root_dir)
        os.makedirs(config_dir, exist_ok=True)
        for filename, payload in configs.items():
            save_json(os.path.join(config_dir, filename), payload)

        floorplan_dir = _floorplan_dir(root_dir)
        os.makedirs(floorplan_dir, exist_ok=True)
        for name in os.listdir(floorplan_dir):
            path = os.path.join(floorplan_dir, name)
            ext = os.path.splitext(name)[1].lower()
            if os.path.isfile(path) and ext in ALLOWED_FLOORPLAN_EXTENSIONS:
                os.remove(path)
        for filename, payload in assets.items():
            with open(os.path.join(floorplan_dir, filename), "wb") as handle:
                handle.write(payload)

    return {
        "status": "ok",
        "manifest": manifest,
        "backup_path": backup_path,
        "imported_config_files": sorted(configs),
        "imported_floorplan_assets": sorted(assets),
    }


def config_bundle_summary(root_dir: Optional[str] = None) -> Dict[str, Any]:
    root_dir = root_dir or project_root()
    config_files = [name for name, _ in _iter_config_files(root_dir)]
    assets = [name for name, _ in _iter_floorplan_assets(root_dir)]
    diagnostics = [name for name, _ in _diagnostic_files(root_dir)]
    try:
        system = load_json(os.path.join(_config_dir(root_dir), "system_config.json"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        system = {}
    return {
        "config_files": config_files,
        "floorplan_assets": assets,
        "diagnostics": diagnostics,
        "sensor_count": len(system.get("sensors") or []),
        "system_name": (system.get("system") or {}).get("name"),
        "config_version": system.get("config_version"),
    }
