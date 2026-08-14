"""Единое определение путей — корректно и при запуске из исходников, и из собранного EXE.

Проблема: при сборке PyInstaller-ом `__file__` указывает внутрь временного каталога
распаковки (`sys._MEIPASS`), который эфемерен и не предназначен для записи. Поэтому
рабочие данные (`data/`, `logs/`, `.run/`) должны лежать РЯДОМ С EXE, а не в _MEIPASS.

Два разных корня:
  * app_root()    — куда ПИСАТЬ: каталог EXE (frozen) либо корень проекта (исходники);
  * bundle_root() — откуда ЧИТАТЬ упакованные ресурсы (шаблоны, статика, дефолтные
                    конфиги): `sys._MEIPASS` (frozen) либо корень проекта.

Переопределение корня данных: переменная окружения `KVT_HOME`.
"""
import os
import sys


def is_frozen() -> bool:
    """Запущены ли мы из собранного PyInstaller-ом исполняемого файла."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> str:
    """Каталог упакованных read-only ресурсов (шаблоны, статика, дефолтные конфиги)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_root() -> str:
    """Каталог для записи рабочих данных: data/, logs/, .run/.

    Приоритет: KVT_HOME → каталог EXE (frozen) → корень проекта (исходники).
    """
    env_home = os.environ.get("KVT_HOME", "").strip()
    if env_home:
        return os.path.abspath(env_home)
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    return os.path.join(app_root(), "data")


def config_dir() -> str:
    return os.path.join(data_dir(), "config")


def logs_dir() -> str:
    return os.path.join(app_root(), "logs")


def run_dir() -> str:
    return os.path.join(app_root(), ".run")


def bundled_resource(*parts) -> str:
    """Путь к упакованному ресурсу внутри бандла (или в дереве исходников)."""
    return os.path.join(bundle_root(), *parts)


def ensure_dirs() -> None:
    for path in (data_dir(), config_dir(), logs_dir(), run_dir()):
        os.makedirs(path, exist_ok=True)


# Список конфигураций, которые должны существовать до старта сервисов.
# Часть из них config_manager создаёт сам по умолчанию, но system_config.json —
# обязателен (load_system_config падает при его отсутствии).
SEEDED_CONFIGS = (
    "system_config.json",
    "poller_config.json",
    "archive_config.json",
    "opcua_config.json",
    "mqtt_config.json",
    "notifications.json",
    "theme_config.json",
    "layout.json",
    "floorplan_config.json",
    "mnemo_tree.json",
    "reports_config.json",
    "operators.json",
    "holidays.json",
)


def seed_default_configs() -> list:
    """Развернуть дефолтные конфиги рядом с EXE при первом запуске.

    Копирует только отсутствующие файлы — пользовательские настройки не затирает.
    Возвращает список скопированных имён.
    """
    import shutil

    ensure_dirs()
    src_dir = bundled_resource("default_config")
    if not os.path.isdir(src_dir):
        return []
    copied = []
    for name in SEEDED_CONFIGS:
        src = os.path.join(src_dir, name)
        dst = os.path.join(config_dir(), name)
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        try:
            shutil.copyfile(src, dst)
            copied.append(name)
        except OSError:
            pass
    return copied
