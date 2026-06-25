from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioFileItem:
    status: str
    size: int | None
    folder: str
    filename: str
    path: Path | None = None
    reference: str | None = None
    duplicate_key: str | None = None
