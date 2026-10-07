"""Resolve media tools for both PATH-based installs and Windows app launches."""
import os
from pathlib import Path
import shutil
import sys


def _windows_install_path():
    if sys.platform != "win32":
        return ""
    import winreg

    paths = []
    for root, key in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(root, key) as registry:
                value, _ = winreg.QueryValueEx(registry, "Path")
                paths.append(os.path.expandvars(value))
        except FileNotFoundError:
            continue
    return os.pathsep.join(paths)


def media_executable(name):
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError(f"Unsupported media executable: {name}")
    configured = os.getenv(f"{name.upper()}_BINARY")
    if configured:
        executable = shutil.which(configured)
        if executable:
            return executable
        raise RuntimeError(f"{name.upper()}_BINARY does not point to an executable: {configured}")
    executable = shutil.which(name)
    if executable:
        return executable
    directories = [str(Path(sys.prefix) / "Library" / "bin"), str(Path(sys.prefix) / "bin")]
    installed_path = _windows_install_path()
    if installed_path:
        directories.append(installed_path)
    executable = shutil.which(name, path=os.pathsep.join(directories))
    if executable:
        return executable
    raise RuntimeError(
        f"Install FFmpeg (including {name}) and restart the server, "
        f"or set {name.upper()}_BINARY to its executable path."
    )
