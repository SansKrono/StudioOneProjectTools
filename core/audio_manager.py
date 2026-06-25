from pathlib import Path
import hashlib
import os
import re
from models.audio_file import AudioFileItem


AUDIO_EXTENSIONS = {
    ".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a", ".wma"
}


AUDIO_REF_PATTERN = re.compile(
    r'([A-Za-z]:\\[^"<>\n\r]+?\.(?:wav|wave|aif|aiff|flac|mp3|ogg|m4a|wma)|[^"<>\n\r/\\]+?\.(?:wav|wave|aif|aiff|flac|mp3|ogg|m4a|wma))',
    re.IGNORECASE,
)


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return str(size)


def quick_audio_fingerprint(path: Path, edge_bytes: int = 1024 * 1024) -> tuple[int, str]:
    size = path.stat().st_size
    h = hashlib.md5()
    h.update(str(size).encode("ascii"))

    with path.open("rb") as f:
        h.update(f.read(edge_bytes))
        if size > edge_bytes:
            f.seek(max(0, size - edge_bytes))
            h.update(f.read(edge_bytes))

    return size, h.hexdigest()


def list_project_audio_files(project_dir: Path) -> list[Path]:
    files = []
    for p in project_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
            files.append(p)
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


def resolve_audio_reference(ref: str, project_dir: Path) -> Path | None:
    p = Path(ref)

    if p.is_absolute() and p.exists():
        return p

    candidate = project_dir / ref
    if candidate.exists():
        return candidate

    # Windows paths inside XML can contain backslashes even if Path is running normally.
    candidate = project_dir / ref.replace("\\", os.sep)
    if candidate.exists():
        return candidate

    name = Path(ref.replace("\\", "/")).name
    if name:
        for found in project_dir.rglob(name):
            if found.is_file():
                return found

    return None


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

            key = f"{size}:{digest}"
            groups.setdefault(key, []).append(p)

    return {k: v for k, v in groups.items() if len(v) > 1}


def build_audio_table_rows(project_dir: Path, archive_texts: list[str]) -> list[AudioFileItem]:
    all_text = "\n".join(archive_texts)
    refs = extract_audio_references_from_text(all_text)

    rows: list[AudioFileItem] = []
    used_paths: set[Path] = set()
    used_names: set[str] = set()

    for ref in refs:
        resolved = resolve_audio_reference(ref, project_dir)
        filename = Path(ref.replace("\\", "/")).name

        if resolved:
            try:
                size = resolved.stat().st_size
            except OSError:
                size = None

            status = "NORMAL"
            try:
                if resolved.is_absolute() and not resolved.resolve().is_relative_to(project_dir.resolve()):
                    status = "EXTERNAL"
            except Exception:
                pass

            rows.append(AudioFileItem(status, size, str(resolved.parent), resolved.name, resolved, ref))
            used_paths.add(resolved.resolve())
            used_names.add(resolved.name.lower())
        else:
            rows.append(AudioFileItem("MISSING", None, "", filename, None, ref))
            used_names.add(filename.lower())

    local_files = list_project_audio_files(project_dir)
    duplicate_groups = detect_duplicate_audio(local_files)
    duplicate_paths: set[Path] = set()
    duplicate_key_by_path: dict[Path, str] = {}

    for key, files in duplicate_groups.items():
        for p in files:
            rp = p.resolve()
            duplicate_paths.add(rp)
            duplicate_key_by_path[rp] = key

    existing_row_paths = {r.path.resolve() for r in rows if r.path}

    for p in local_files:
        rp = p.resolve()
        if rp in existing_row_paths:
            continue

        status = "UNUSED" if p.name.lower() not in used_names else "NORMAL"
        rows.append(AudioFileItem(status, p.stat().st_size, str(p.parent), p.name, p, None))

    for r in rows:
        if r.path:
            rp = r.path.resolve()
            if rp in duplicate_paths:
                # Preserve UNUSED priority.
                # A file that is not referenced by the project must stay UNUSED,
                # even if its audio content is also a duplicate of another file.
                # The duplicate_key is still stored for later reporting/cleanup.
                r.duplicate_key = duplicate_key_by_path.get(rp)
                if r.status != "UNUSED":
                    r.status = "DUPLICATE"

    return rows
