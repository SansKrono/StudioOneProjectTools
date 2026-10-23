from pathlib import Path
import shutil

from core.audio_manager import quick_audio_fingerprint
from core.common import filename_from_reference, unique_path
from core.everything import search_everything_by_filename
from core.relink import Relinker, relative_ref
from core.song_archive import write_modified_song_copy
from models.missing_file import MissingFileCandidate


def get_missing_audio_rows(audio_rows) -> list:
    return [r for r in audio_rows if r.status == "MISSING"]


def search_missing_candidates_everything(audio_rows, max_results_per_file: int = 12, es_path: str | None = None) -> list[MissingFileCandidate]:
    """
    Search missing audio via Everything.

    - each unique filename is searched only once;
    - candidates are limited per filename;
    - a quick hash is only calculated when several same-named candidates exist.
    """
    candidates: list[MissingFileCandidate] = []

    rows_by_filename: dict[str, list] = {}
    for row in get_missing_audio_rows(audio_rows):
        ref = row.reference or row.filename
        filename = filename_from_reference(ref)
        if filename:
            rows_by_filename.setdefault(filename.lower(), []).append((ref, filename))

    for items in rows_by_filename.values():
        found_paths = search_everything_by_filename(items[0][1], max_results=max_results_per_file, es_path=es_path)

        need_hash = len(found_paths) > 1
        path_infos = []
        for p in found_paths:
            try:
                size = p.stat().st_size
            except OSError:
                size = None

            digest = None
            if need_hash:
                try:
                    size, digest = quick_audio_fingerprint(p)
                except OSError:
                    digest = None

            if not need_hash:
                confidence = "FILENAME ONLY"
            elif digest:
                confidence = "FILENAME + SIZE + QUICK HASH"
            else:
                confidence = "FILENAME + SIZE"
            path_infos.append((p, size, digest, confidence))

        for ref, row_filename in items:
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
    """Copy candidates into media_dir. Returns missing_reference -> copied destination path."""
    media_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, Path] = {}

    for candidate in candidates:
        dest = unique_path(media_dir / candidate.source_path.name)
        shutil.copy2(candidate.source_path, dest)
        mapping[candidate.missing_reference] = dest

    return mapping


def relink_song_copy_to_media(song_path: Path, output_path: Path, reference_to_dest: dict[str, Path], project_dir: Path) -> tuple[Path, int]:
    """
    Write a new .song where the given missing references point at their recovered copies.

    The original .song is never overwritten; only text members are edited.
    Returns (output_path, number of replacements made).
    """
    relinker = Relinker()
    for ref, dest in reference_to_dest.items():
        relinker.add_reference(ref, relative_ref(dest, project_dir))

    hits = 0

    def transform(_name, text):
        nonlocal hits
        new_text, replaced = relinker.apply(text)
        hits += len(replaced)
        return new_text

    write_modified_song_copy(song_path, output_path, transform)
    return output_path, hits


def recover_missing_to_media_and_relink(song_path: Path, output_path: Path, project_dir: Path, selected_candidates: list[MissingFileCandidate], media_folder_name: str = "Media") -> tuple[Path, dict[str, Path]]:
    reference_to_dest = copy_candidates_to_media(selected_candidates, project_dir / media_folder_name)
    relink_song_copy_to_media(song_path, output_path, reference_to_dest, project_dir)
    return output_path, reference_to_dest
