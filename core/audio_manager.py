from pathlib import Path
import hashlib
import os
import re

from core.common import filename_from_reference, format_bytes
from models.audio_file import AudioFileItem


AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a", ".wma"
}

# Longest first plus a trailing boundary, so "beat.aiff" is not cut to "beat.aif".
_EXT_ALTERNATION = "|".join(sorted((e.lstrip(".") for e in AUDIO_EXTENSIONS), key=len, reverse=True))

# An absolute Windows path (C:\...\file.wav), or a relative backslash path / bare
# file name (Media\file.wav, file.wav). Forward-slash paths and URLs are reduced to
# their file name and resolved by name inside the project.
AUDIO_REF_PATTERN = re.compile(
    rf'([A-Za-z]:\\[^"<>\n\r]+?\.(?:{_EXT_ALTERNATION})|[^"<>\n\r/:]+?\.(?:{_EXT_ALTERNATION}))(?![A-Za-z0-9])',
    re.IGNORECASE,
)

# Kept for backwards compatibility with older callers.
format_size = format_bytes


def quick_audio_fingerprint(path: Path, edge_bytes: int = 1024 * 1024) -> tuple[int, str]:
    """Size plus an MD5 of the first and last MB. Used only to compare files, not for security."""
    size = path.stat().st_size
    h = hashlib.md5(usedforsecurity=False)
    h.update(str(size).encode("ascii"))

    with path.open("rb") as f:
        h.update(f.read(edge_bytes))
        if size > edge_bytes:
            f.seek(max(0, size - edge_bytes))
            h.update(f.read(edge_bytes))

    return size, h.hexdigest()


def list_project_audio_files(project_dir: Path) -> list[Path]:
    files = []
    for root, _dirs, names in os.walk(project_dir):
        for name in names:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                files.append(Path(root, name))
    return files


def extract_audio_references_from_text(text: str) -> list[str]:
    refs = []
    seen = set()
    for m in AUDIO_REF_PATTERN.finditer(text):
        ref = m.group(1)
        key = ref.lower()
        if key not in seen:
            seen.add(key)
            refs.append(ref)
    return refs


def _index_by_name(files: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for p in files:
        index.setdefault(p.name.lower(), p)
    return index


def _resolve(ref: str, project_dir: Path, name_index: dict[str, Path]) -> tuple[Path | None, bool]:
    """Return (path, resolved_by_name_only)."""
    p = Path(ref)

    if p.is_absolute() and p.exists():
        return p, False

    # Windows paths inside XML use backslashes even on other platforms.
    candidate = project_dir / ref.replace("\\", os.sep)
    if candidate.exists():
        return candidate, False

    name = filename_from_reference(ref)
    found = name_index.get(name.lower()) if name else None
    return found, found is not None


def resolve_audio_reference(ref: str, project_dir: Path, name_index: dict[str, Path] | None = None) -> Path | None:
    if name_index is None:
        name_index = _index_by_name(list_project_audio_files(project_dir))
    return _resolve(ref, project_dir, name_index)[0]


def detect_duplicate_audio(files: list[Path]) -> dict[str, list[Path]]:
    by_size: dict[int, list[Path]] = {}
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        by_size.setdefault(size, []).append(p)

    groups: dict[str, list[Path]] = {}

    for size, same_size in by_size.items():
        if len(same_size) < 2:
            continue

        for p in same_size:
            try:
                _, digest = quick_audio_fingerprint(p)
            except OSError:
                continue

            groups.setdefault(f"{size}:{digest}", []).append(p)

    return {k: v for k, v in groups.items() if len(v) > 1}


def _file_size(p: Path) -> int | None:
    try:
        return p.stat().st_size
    except OSError:
        return None


def build_audio_table_rows(project_dir: Path, archive_texts: list[str]) -> list[AudioFileItem]:
    project_root = project_dir.resolve()
    local_files = list_project_audio_files(project_dir)
    name_index = _index_by_name(local_files)

    refs = extract_audio_references_from_text("\n".join(archive_texts))

    rows: list[AudioFileItem] = []
    resolved_of: dict[int, Path] = {}
    used_names: set[str] = set()

    for ref in refs:
        resolved, by_name = _resolve(ref, project_dir, name_index)
        filename = filename_from_reference(ref)

        if resolved:
            rp = resolved.resolve()
            status = "NORMAL" if (not resolved.is_absolute() or rp.is_relative_to(project_root)) else "EXTERNAL"
            row = AudioFileItem(status, _file_size(resolved), str(resolved.parent), resolved.name, resolved, ref,
                                resolved_by_name=by_name)
            resolved_of[id(row)] = rp
            used_names.add(resolved.name.lower())
        else:
            row = AudioFileItem("MISSING", None, "", filename, None, ref)
            used_names.add(filename.lower())
        rows.append(row)

    existing_row_paths = set(resolved_of.values())

    for p in local_files:
        rp = p.resolve()
        if rp in existing_row_paths:
            continue

        status = "UNUSED" if p.name.lower() not in used_names else "NORMAL"
        row = AudioFileItem(status, _file_size(p), str(p.parent), p.name, p, None)
        resolved_of[id(row)] = rp
        rows.append(row)

    duplicate_key_by_path: dict[Path, str] = {}
    for key, files in detect_duplicate_audio(local_files).items():
        for p in files:
            duplicate_key_by_path[p.resolve()] = key

    for r in rows:
        rp = resolved_of.get(id(r))
        key = duplicate_key_by_path.get(rp) if rp else None
        if key:
            # A file that is not referenced by the project stays UNUSED even if its
            # content duplicates another file; the duplicate_key is still recorded.
            r.duplicate_key = key
            if r.status != "UNUSED":
                r.status = "DUPLICATE"

    return rows
