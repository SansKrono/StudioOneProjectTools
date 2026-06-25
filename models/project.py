from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectSummary:
    song_path: Path | None = None
    project_dir: Path | None = None

    tracks_count: int = 0
    plugin_count: int = 0
    bypassed_fx_count: int = 0

    audio_refs_count: int = 0
    missing_audio_count: int = 0
    duplicate_audio_count: int = 0
    unused_audio_count: int = 0
    external_audio_count: int = 0

    project_size_bytes: int = 0

    messages: list[str] = field(default_factory=list)
