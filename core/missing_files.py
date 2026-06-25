from pathlib import Path
import zipfile
import shutil
from core.audio_manager import AUDIO_EXTENSIONS, quick_audio_fingerprint
from core.everything import search_everything_by_filename, find_es_exe
from core.consolidate import copy_file_to_media
from models.missing_file import MissingFileCandidate


TEXT_MEMBER_SUFFIXES = (
    ".xml",
    ".txt",
    ".settings",
    ".songdata",
    ".json",
)


def filename_from_reference(ref: str) -> str:
    return Path(ref.replace("\\", "/")).name


def get_missing_audio_rows(audio_rows) -> list:
    return [r for r in audio_rows if r.status == "MISSING"]


def search_missing_candidates_everything(audio_rows, max_results_per_file: int = 12, es_path: str | None = None, instance: str | None = None) -> list[MissingFileCandidate]:
    """
    Search missing audio via Everything.

    Performance rules:
    - each unique filename is searched only once;
    - candidates are limited;
    - if there is only one exact filename match, no quick hash is calculated;
    - quick hash is calculated only when several candidates with the same filename exist.
    """
    candidates: list[MissingFileCandidate] = []

    rows_by_filename: dict[str, list] = {}
    for row in get_missing_audio_rows(audio_rows):
        ref = row.reference or row.filename
        filename = filename_from_reference(ref)
        if not filename:
            continue
        rows_by_filename.setdefault(filename.lower(), []).append((row, ref, filename))

    for _key, items in rows_by_filename.items():
        _row0, _ref0, filename = items[0]
        found_paths = search_everything_by_filename(filename, max_results=max_results_per_file, es_path=es_path, instance=instance, mode=mode)

        exact_paths = []
        seen = set()
        for p in found_paths:
            try:
                if not p.is_file():
                    continue
                if p.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                if p.name.lower() != filename.lower():
                    continue
                key = str(p).lower()
                if key in seen:
                    continue
                seen.add(key)
                exact_paths.append(p)
            except Exception:
                continue

        need_hash = len(exact_paths) > 1
        path_infos = []
        for p in exact_paths:
            try:
                size = p.stat().st_size
            except Exception:
                size = None

            digest = None
            if need_hash:
                try:
                    _size, digest = quick_audio_fingerprint(p)
                    if size is None:
                        size = _size
                except Exception:
                    digest = None

            confidence = "FILENAME ONLY" if not need_hash else ("FILENAME + SIZE + QUICK HASH" if digest else "FILENAME + SIZE")
            path_infos.append((p, size, digest, confidence))

        for row, ref, row_filename in items:
            for p, size, digest, confidence in path_infos:
                candidates.append(
                    MissingFileCandidate(
                        missing_reference=ref,
                        filename=row_filename,
                        source_path=p,
                        size=size,
                        quick_hash=digest,
                        confidence=confidence,
                    )
                )

    return candidates

def copy_candidates_to_media(candidates: list[MissingFileCandidate], media_dir: Path) -> dict[str, Path]:
    """
    Copies selected candidates to the project Media folder.

    Returns mapping:
      missing_reference -> copied_destination_path
    """
    mapping: dict[str, Path] = {}

    for candidate in candidates:
        dest = copy_file_to_media(candidate.source_path, media_dir)
        mapping[candidate.missing_reference] = dest

    return mapping


def relink_song_copy_to_media(song_path: Path, output_path: Path, reference_to_dest: dict[str, Path], project_dir: Path) -> Path:
    """
    Creates a new .song archive where exact missing references are replaced by local Media relative paths.

    Conservative v1 behavior:
    - original .song is never overwritten;
    - only text-like archive members are edited;
    - replacement is exact-string based.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build replacement strings as Windows-style relative paths because Studio One projects on Windows
    # usually store paths this way.
    replacements: dict[str, str] = {}

    for ref, dest in reference_to_dest.items():
        try:
            rel = dest.resolve().relative_to(project_dir.resolve())
            new_ref = str(rel).replace("/", "\\")
        except Exception:
            new_ref = str(dest).replace("/", "\\")

        replacements[ref] = new_ref

        # Also try normalized slash variants.
        replacements[ref.replace("\\", "/")] = new_ref.replace("\\", "/")
        replacements[filename_from_reference(ref)] = new_ref

    with zipfile.ZipFile(song_path, "r") as zin, zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            lower = info.filename.lower()

            if lower.endswith(TEXT_MEMBER_SUFFIXES):
                try:
                    text = data.decode("utf-8", errors="replace")
                except Exception:
                    zout.writestr(info, data)
                    continue

                new_text = text
                for old, new in replacements.items():
                    if old:
                        new_text = new_text.replace(old, new)

                zout.writestr(info, new_text.encode("utf-8"))
            else:
                zout.writestr(info, data)

    return output_path


def recover_missing_to_media_and_relink(song_path: Path, output_path: Path, project_dir: Path, selected_candidates: list[MissingFileCandidate], media_folder_name: str = "Media") -> tuple[Path, dict[str, Path]]:
    media_dir = project_dir / media_folder_name
    reference_to_dest = copy_candidates_to_media(selected_candidates, media_dir)
    relink_song_copy_to_media(song_path, output_path, reference_to_dest, project_dir)
    return output_path, reference_to_dest
