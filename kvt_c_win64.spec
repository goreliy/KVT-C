# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, SPECPATH)
from shared.paths import SEEDED_CONFIGS
from shared.notifications import defaults as notification_defaults

# Only seed JSON files belong in the portable executable, never local keys or backups.
default_configs = tempfile.TemporaryDirectory(prefix="kvt-build-defaults-")
for filename in SEEDED_CONFIGS:
    source = os.path.join(SPECPATH, "data", "config", filename)
    destination = os.path.join(default_configs.name, filename)
    if filename == "notifications.json":
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(notification_defaults(), handle, ensure_ascii=False, indent=2)
    elif os.path.isfile(source):
        shutil.copyfile(source, destination)

block_cipher = None

service_packages = [
    "archiver",
    "mqtt_bridge",
    "opcua_server",
    "poller",
    "shared",
    "visualizer",
    "MocTestServer.server",
]

hiddenimports = []
for package in service_packages:
    hiddenimports += collect_submodules(package)


a = Analysis(
    ["run_kvt.py"],
    pathex=["."],
    binaries=[],
    datas=[
        (default_configs.name, "default_config"),
        ("visualizer/templates", "visualizer/templates"),
        ("visualizer/static", "visualizer/static"),
        ("MocTestServer/server/templates", "MocTestServer/server/templates"),
        ("MocTestServer/server/static", "MocTestServer/server/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "PIL", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="KVT-C",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
default_configs.cleanup()

