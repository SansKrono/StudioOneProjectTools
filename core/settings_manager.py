from pathlib import Path
import json

from core.common import app_dir


DEFAULT_SETTINGS = {
    # Empty means auto-detect (see core.everything.find_es_exe).
    "everything_es_path": "",
    "media_folder_name": "Media",
    "duplicates_folder_name": "_Duplicates",
    "unused_folder_name": "_Unused",
    # Empty means auto (see core.crash_analyzer.crash_search_roots).
    "studio_one_data_root": "",
}


def settings_file_path() -> Path:
    return app_dir() / "settings.json"


def _known(data: dict) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: str(v) for k, v in data.items() if k in DEFAULT_SETTINGS and isinstance(v, (str, int, float))})
    return settings


def load_settings() -> dict:
    try:
        data = json.loads(settings_file_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    return _known(data) if isinstance(data, dict) else dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> Path:
    path = settings_file_path()
    path.write_text(json.dumps(_known(settings), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
