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
    # True when the stored reference did not resolve and the file was found by name only.
    resolved_by_name: bool = False
