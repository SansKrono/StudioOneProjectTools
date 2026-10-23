"""Small helpers shared by the core modules and the GUI."""

from pathlib import Path
import sys


def app_dir() -> Path:
    """Folder that holds the app (the EXE folder when frozen, the repo root otherwise)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def format_bytes(num: int | float | None) -> str:
    if num is None:
        return ""
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def filename_from_reference(ref: str) -> str:
    return Path(ref.replace("\\", "/")).name


def unique_path(dest: Path) -> Path:
    """Return dest, or dest with a _1, _2, ... suffix if something already exists there."""
    if not dest.exists():
        return dest

    i = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def is_inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False
