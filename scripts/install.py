#!/usr/bin/env python3
"""Install Omakeeb for one user; never alter system packages or user backups."""
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

APP_ID = "org.omarchy.Omakeeb"
MARKER = ".omakeeb-install.json"
LAUNCHER_MARKER = "# Managed by Omakeeb installer"
DESKTOP_MARKER = "X-Omakeeb-Managed=true"
SOURCE = Path(__file__).resolve().parent.parent


def destinations(data_home, bin_dir):
    data_home, bin_dir = Path(data_home).expanduser().absolute(), Path(bin_dir).expanduser().absolute()
    for path in (data_home, bin_dir):
        if any(char in str(path) for char in "\n\r\0"):
            raise ValueError("Installation paths cannot contain control characters.")
    return {
        "app": data_home / "omakeeb/app",
        "launcher": bin_dir / "omakeeb",
        "desktop": data_home / f"applications/{APP_ID}.desktop",
        "icon": data_home / f"icons/hicolor/scalable/apps/{APP_ID}.svg",
    }


def check_dependencies():
    result = subprocess.run([sys.executable, "-c",
        'import gi, cairo; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 10), "GTK 4.10 or newer is required"'],
        capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("GTK 4 Python bindings are missing. Install gtk4, python-gobject and python-cairo with Omarchy's package manager.\n" + result.stderr.strip())
    if not ctypes.util.find_library("hidapi-hidraw"):
        raise RuntimeError("libhidapi-hidraw is missing. Install the hidapi package.")


def _owned_app(path):
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        data = json.loads((path / MARKER).read_text())
        return data.get("application_id") == APP_ID and data.get("version") == 1
    except (OSError, ValueError, AttributeError):
        return False


def _owned_file(path, marker):
    if path.is_symlink() or not path.is_file():
        return False
    try:
        return marker in path.read_text()
    except (OSError, UnicodeError):
        return False


def _check_destinations(paths):
    if paths["app"].exists() or paths["app"].is_symlink():
        if not _owned_app(paths["app"]):
            raise RuntimeError(f"Refusing to replace an unmanaged application directory: {paths['app']}")
    for key, marker in (("launcher", LAUNCHER_MARKER), ("desktop", DESKTOP_MARKER), ("icon", "Omakeeb application icon")):
        path = paths[key]
        if path.exists() or path.is_symlink():
            if not _owned_file(path, marker):
                raise RuntimeError(f"Refusing to replace an unmanaged file: {path}")


def _write_atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _desktop_exec(path):
    # Escape the command-line argument, then the Desktop Entry string layer.
    argument = str(path).replace("%", "%%")
    argument = "".join("\\" + c if c in '\\"`$' else c for c in argument)
    return '"' + argument.replace("\\", "\\\\") + '"'


def install(source=SOURCE, data_home=None, bin_dir=None, *, dependencies=True):
    data_home = data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
    bin_dir = bin_dir or Path.home() / ".local/bin"
    paths = destinations(data_home, bin_dir)
    _check_destinations(paths)
    if dependencies:
        check_dependencies()
    source = Path(source).resolve()
    for required in ("omakeeb", "resources", "scripts"):
        if not (source / required).is_dir():
            raise RuntimeError(f"Incomplete source tree: {source / required}")
    for required in ("README.md", "LICENSE", "THIRD_PARTY.md", f"resources/{APP_ID}.svg"):
        if not (source / required).is_file():
            raise RuntimeError(f"Incomplete source tree: {source / required}")
    paths["app"].parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".app-new-", dir=paths["app"].parent))
    old = paths["app"].parent / (".app-old-" + uuid.uuid4().hex)
    moved_old = False
    promoted = False
    saved_files = {}
    try:
        for folder in ("omakeeb", "resources", "scripts"):
            shutil.copytree(source / folder, staging / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        for name in ("README.md", "LICENSE", "THIRD_PARTY.md"):
            shutil.copy2(source / name, staging / name)
        (staging / "run.py").write_text('from pathlib import Path\nimport runpy\nimport sys\nsys.path.insert(0, str(Path(__file__).resolve().parent))\nrunpy.run_module("omakeeb", run_name="__main__")\n')
        (staging / MARKER).write_text(json.dumps({"application_id":APP_ID,"version":1},indent=2)+"\n")
        # All copies finish before switching the active directory. Keep the
        # previous managed app until the launcher and desktop entry succeed.
        if paths["app"].exists():
            os.replace(paths["app"], old)
            moved_old = True
        os.replace(staging, paths["app"])
        promoted = True
        launcher = f"#!/bin/sh\n{LAUNCHER_MARKER}\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(paths['app'] / 'run.py'))} \"$@\"\n"
        desktop = f"""[Desktop Entry]
Type=Application
Version=1.0
Name=Omakeeb
GenericName=Keyboard Manager
Comment=Configure QMK keyboards, Omarchy shortcuts and macros
Exec={_desktop_exec(paths['launcher'])}
Icon={APP_ID}
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=QMK;VIA;Vial;keyboard;keymap;macro;Omarchy;
StartupNotify=true
StartupWMClass={APP_ID}
{DESKTOP_MARKER}
Actions=Demo;

[Desktop Action Demo]
Name=Explore demo keyboard
Exec={_desktop_exec(paths['launcher'])} --demo
"""
        for key, content, mode in (("launcher",launcher,0o755), ("desktop",desktop,0o644),
                                   ("icon",(source / f"resources/{APP_ID}.svg").read_text(),0o644)):
            path = paths[key]
            saved_files[key] = (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
            _write_atomic(path, content, mode)
        if moved_old:
            shutil.rmtree(old)
        return paths
    except Exception:
        # Roll back only paths owned by this installer. Backups and imported
        # definitions are siblings of app/ and never part of this replacement.
        for key, previous in saved_files.items():
            if previous is None:
                paths[key].unlink(missing_ok=True)
            else:
                _write_atomic(paths[key], *previous)
        if promoted:
            shutil.rmtree(paths["app"])
        if moved_old and old.exists():
            os.replace(old, paths["app"])
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def uninstall(data_home=None, bin_dir=None):
    data_home = data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
    bin_dir = bin_dir or Path.home() / ".local/bin"
    paths = destinations(data_home, bin_dir)
    _check_destinations(paths)
    for key in ("launcher", "desktop", "icon"):
        paths[key].unlink(missing_ok=True)
    if paths["app"].exists():
        shutil.rmtree(paths["app"])
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"), nargs="?", default="install")
    parser.add_argument("--data-home", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")))
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    parser.add_argument("--dry-run", action="store_true", help="Show destination paths without changing anything")
    args = parser.parse_args()
    try:
        if args.dry_run:
            paths = destinations(args.data_home, args.bin_dir)
        elif args.action == "install":
            paths = install(data_home=args.data_home, bin_dir=args.bin_dir)
        else:
            paths = uninstall(args.data_home, args.bin_dir)
        print(f"Omakeeb {args.action}" + (" (dry run)" if args.dry_run else " complete"))
        for key, path in paths.items():
            print(f"  {key}: {path}")
        if args.action == "uninstall":
            print("Backups, firmware archives and imported definitions were preserved.")
        elif not args.dry_run:
            print(f"Open Omakeeb in the application launcher, or run {paths['launcher']}.")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"omakeeb: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
