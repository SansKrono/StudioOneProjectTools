from dataclasses import dataclass
from pathlib import Path


@dataclass
class MissingFileCandidate:
    missing_reference: str
    filename: str
    source_path: Path
    size: int | None
    quick_hash: str | None
    confidence: str
