from pathlib import Path
import json


DEFAULT_SETTINGS = {
    "everything_es_path": "es.exe",
    "everything_instance": "1.5a",
    "media_folder_name": "Media",
    "duplicates_folder_name": "_Duplicates",
    "unused_folder_name": "_Unused",
}


def settings_file_path() -> Path:
    return Path.cwd() / "settings.json"


def load_settings() -> dict:
    path = settings_file_path()
    if not path.exists():
        return dict(DEFAULT_SETTINGS)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_SETTINGS)

    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return settings


def save_settings(settings: dict) -> Path:
    path = settings_file_path()
    data = dict(DEFAULT_SETTINGS)
    data.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
