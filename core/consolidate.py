from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil

from core.audio_manager import build_audio_table_rows
from core.common import format_bytes, is_inside, unique_path
from core.relink import Relinker, relative_ref
from core.song_archive import read_text_members, write_modified_song_copy


@dataclass
class ConsolidateResult:
    output_dir: Path
    output_song: Path
    media_dir: Path
    report_path: Path
    relink_report_path: Path
    relink_debug_report_path: Path
    copied_audio: int = 0
    copied_unused: int = 0
    copied_duplicates: int = 0
    recovered_missing: int = 0
    skipped_missing: int = 0
    skipped_ambiguous: int = 0
    relink_replacements: int = 0
    verify_total_refs_after: int = 0
    verify_missing_after: int = 0
    verify_missing_refs_after: list[str] = field(default_factory=list)
    no_bypassed_song: Path | None = None
    errors: list[str] = field(default_factory=list)


def unique_folder(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 1
    while True:
        candidate = dest.with_name(f"{dest.name}_{i}")
        if not candidate.exists():
            return candidate
        i += 1


def move_file_to_folder(src: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(target_dir / src.name)
    shutil.move(str(src), str(dest))
    return dest


def _emit_progress(progress_callback, **payload):
    if progress_callback:
        try:
            progress_callback(payload)
        except Exception:
            pass


def copy_file_with_progress(src: Path, dest: Path, progress_callback=None, chunk_size: int = 8 * 1024 * 1024) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = src.stat().st_size
    copied = 0
    info = {"current_file": src.name, "current_source": str(src), "current_dest": str(dest), "current_total_bytes": total}

    _emit_progress(progress_callback, phase="copy_file_start", current_copied_bytes=0, **info)

    with src.open("rb") as fsrc, dest.open("wb") as fdst:
        while chunk := fsrc.read(chunk_size):
            fdst.write(chunk)
            copied += len(chunk)
            _emit_progress(progress_callback, phase="copy_file_progress", current_copied_bytes=copied,
                           copied_bytes_delta=len(chunk), **info)

    try:
        shutil.copystat(src, dest)
    except OSError:
        pass

    _emit_progress(progress_callback, phase="copy_file_done", current_copied_bytes=total, **info)
    return dest


def _safe_missing_candidates_for_audio_rows(audio_rows, missing_candidates) -> tuple[list, list[tuple[str, int]], list[str]]:
    """
    Returns (safe_candidates, ambiguous_rows, not_found_refs).

    Safe means exactly one candidate, or several candidates that all share the
    same size + quick hash (identical copies of the same file).
    """
    safe = []
    ambiguous = []
    not_found = []
    seen_refs = set()

    by_ref: dict[str, list] = {}
    for c in missing_candidates or []:
        by_ref.setdefault(c.missing_reference, []).append(c)

    for row in audio_rows:
        if row.status != "MISSING":
            continue

        ref = row.reference or row.filename
        candidates = by_ref.get(ref, [])
        if not candidates:
            not_found.append(ref)
            continue

        signatures = {(c.size, c.quick_hash) for c in candidates}
        identical = len(signatures) == 1 and all(c.size is not None and c.quick_hash for c in candidates)

        if len(candidates) == 1 or identical:
            if ref not in seen_refs:
                safe.append(candidates[0])
                seen_refs.add(ref)
        else:
            ambiguous.append((ref, len(candidates)))

    return safe, ambiguous, not_found


def _destination_for_source(src: Path, project_dir: Path, output_dir: Path) -> Path:
    """
    Preserve project-relative folder structure:
      Project\\Media\\a.wav   -> Consolidated\\Media\\a.wav
      Project\\Bounces\\b.wav -> Consolidated\\Bounces\\b.wav
      External\\x.wav         -> Consolidated\\External\\x.wav
    """
    try:
        return output_dir / src.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return output_dir / "External" / src.name


def verify_consolidated_song(output_song: Path, output_dir: Path) -> tuple[int, list[str]]:
    """
    Re-analyze the consolidated .song against its new folder.

    A reference counts as broken if it does not resolve, or if it contains a
    folder/path but only resolved because a file with the same name exists
    somewhere else in the folder (the stored path itself is wrong).

    Returns (total audio references, broken references).
    """
    try:
        rows = build_audio_table_rows(output_dir, read_text_members(output_song))
    except Exception as e:
        return 0, [f"VERIFY FAILED: {e}"]

    refs = [r for r in rows if r.reference]
    broken = []
    for r in refs:
        has_dir = "\\" in r.reference or "/" in r.reference
        if r.status == "MISSING" or (r.resolved_by_name and has_dir):
            broken.append(r.reference)
        elif r.path and not is_inside(r.path, output_dir):
            broken.append(f"{r.reference}  (still points outside the consolidated folder)")
    return len(refs), broken


def consolidate_project_to_new_folder(
    *,
    song_path: Path,
    project_dir: Path,
    output_parent: Path,
    audio_rows,
    missing_candidates=None,
    media_folder_name: str = "Media",
    duplicates_folder_name: str = "_Duplicates",
    unused_folder_name: str = "_Unused",
    recover_missing: bool = True,
    copy_unused: bool = False,
    copy_duplicate_nonreferenced: bool = True,
    progress_callback=None,
) -> ConsolidateResult:
    """
    Safe preserve-folders consolidate.

    - the .song is copied byte-for-byte first;
    - project-relative audio keeps its relative folder path;
    - only external and recovered-missing files are relinked;
    - the original project is never modified (everything is copied, nothing moved).
    """
    output_dir = unique_folder(Path(output_parent) / f"{song_path.stem}_CONSOLIDATED")
    media_dir = output_dir / media_folder_name
    output_song = output_dir / f"{song_path.stem}_CONSOLIDATED.song"

    output_dir.mkdir(parents=True, exist_ok=False)
    media_dir.mkdir(parents=True, exist_ok=True)

    result = ConsolidateResult(
        output_dir=output_dir,
        output_song=output_song,
        media_dir=media_dir,
        report_path=output_dir / "consolidate_report.txt",
        relink_report_path=output_dir / "relink_report.txt",
        relink_debug_report_path=output_dir / "relink_debug_report.txt",
    )

    relink_lines = [
        "Studio One Project Tools - Relink Report",
        "=" * 80,
        "Preserve-folders mode: files inside the project keep their relative paths and need no relink.",
        "Only external and recovered files are relinked.",
        "",
    ]
    debug_lines = ["Studio One Project Tools - Relink Debug Report", "=" * 80, ""]

    _emit_progress(progress_callback, phase="write_song", message="Copying .song byte-for-byte...")
    shutil.copy2(song_path, output_song)

    # Referenced, existing audio: one copy per resolved file.
    referenced_plan = []
    seen = set()
    for row in audio_rows:
        if row.status == "MISSING" or not row.reference or not row.path or not row.path.exists():
            continue
        key = str(row.path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            referenced_plan.append(row)

    total_copy_bytes = sum(row.size or 0 for row in referenced_plan)
    _emit_progress(progress_callback, phase="plan", total_files=len(referenced_plan), total_bytes=total_copy_bytes,
                   message=f"Copy plan: {len(referenced_plan)} files / {format_bytes(total_copy_bytes)}")

    relinker = Relinker()

    for row in referenced_plan:
        try:
            src = row.path.resolve()
            dest = unique_path(_destination_for_source(src, project_dir, output_dir))

            _emit_progress(progress_callback, phase="copy_referenced", file_index=result.copied_audio + 1,
                           total_files=len(referenced_plan), total_bytes=total_copy_bytes,
                           message=f"Copying {result.copied_audio + 1}/{len(referenced_plan)}: {src.name}")
            copy_file_with_progress(src, dest, progress_callback=progress_callback)
            result.copied_audio += 1

            is_external = not is_inside(src, project_dir)
            if is_external:
                new_ref = relative_ref(dest, output_dir)
                relinker.add_path(str(src), new_ref)
                relinker.add_path(row.reference, new_ref)
                relink_lines += ["-" * 80, f"External source: {src}", f"Copied to:       {dest}", f"New ref:         {new_ref}"]

            debug_lines += ["-" * 80, f"REFERENCED: {row.filename}", f"Old reference: {row.reference}",
                            f"Copied source: {src}", f"Copied dest:   {dest}", f"Relink needed: {'yes' if is_external else 'no'}"]
        except OSError as e:
            result.errors.append(f"Copy referenced audio failed: {row.filename}: {e}")

    # Recover safe missing candidates into Media and relink them.
    ambiguous: list[tuple[str, int]] = []
    not_found: list[str] = []
    if recover_missing:
        safe_missing, ambiguous, not_found = _safe_missing_candidates_for_audio_rows(audio_rows, missing_candidates)
        result.skipped_ambiguous = len(ambiguous)
        result.skipped_missing = len(not_found)

        for c in safe_missing:
            try:
                dest = unique_path(media_dir / c.filename)
                _emit_progress(progress_callback, phase="recover_missing", file_index=result.recovered_missing + 1,
                               total_files=len(safe_missing),
                               message=f"Recovering missing {result.recovered_missing + 1}/{len(safe_missing)}: {c.filename}")
                copy_file_with_progress(c.source_path, dest, progress_callback=progress_callback)
                result.recovered_missing += 1

                new_ref = relative_ref(dest, output_dir)
                relinker.add_reference(c.missing_reference, new_ref)
                relink_lines += ["-" * 80, f"Recovered missing: {c.missing_reference}", f"Found source:      {c.source_path}",
                                 f"Copied to:         {dest}", f"New ref:           {new_ref}"]
            except OSError as e:
                result.errors.append(f"Recover missing failed: {c.filename}: {e}")

    # Optional side copies. These never affect the .song.
    for row in audio_rows:
        if not row.path or not row.path.exists():
            continue
        if row.status == "UNUSED" and copy_unused:
            target, label, counter = output_dir / unused_folder_name, "unused", "copied_unused"
        elif row.status == "DUPLICATE" and copy_duplicate_nonreferenced and not row.reference:
            target, label, counter = output_dir / duplicates_folder_name, "duplicate side copy", "copied_duplicates"
        else:
            continue
        try:
            _emit_progress(progress_callback, phase=f"copy_{label.split()[0]}", message=f"Copying {label}: {row.filename}")
            copy_file_with_progress(row.path, unique_path(target / row.path.name), progress_callback=progress_callback)
            setattr(result, counter, getattr(result, counter) + 1)
        except OSError as e:
            result.errors.append(f"Copy {label} failed: {row.filename}: {e}")

    # Relink only when needed; otherwise the .song stays byte-identical to the original.
    if relinker:
        _emit_progress(progress_callback, phase="write_song", message="Relinking external/recovered files...")

        def transform(name, text):
            new_text, hits = relinker.apply(text)
            for old, new in hits:
                debug_lines.append(f"HIT {name}: {old!r} -> {new!r}")
            result.relink_replacements += len(hits)
            return new_text

        relinked = output_song.with_name(output_song.stem + ".relinked.song")
        write_modified_song_copy(output_song, relinked, transform)
        relinked.replace(output_song)

    _emit_progress(progress_callback, phase="verify", message="Verifying consolidated .song...")
    result.verify_total_refs_after, result.verify_missing_refs_after = verify_consolidated_song(output_song, output_dir)
    result.verify_missing_after = len(result.verify_missing_refs_after)

    report = [
        "Studio One Project Tools - Consolidate Report",
        "=" * 80,
        f"Source song:       {song_path}",
        f"Source folder:     {project_dir}",
        f"Output folder:     {output_dir}",
        f"Output song:       {output_song}",
        "",
        "Mode: SAFE PRESERVE FOLDERS.",
        "The .song was copied byte-for-byte first; only external/recovered references were relinked.",
        "The original project was not modified.",
        "",
        "Post-check",
        "-" * 80,
        f"Audio refs in consolidated .song: {result.verify_total_refs_after}",
        f"Broken refs after consolidate:    {result.verify_missing_after}",
    ]
    if result.verify_missing_refs_after:
        report += ["", "Broken refs after consolidate:"]
        report += [str(ref) for ref in result.verify_missing_refs_after[:200]]
        if len(result.verify_missing_refs_after) > 200:
            report.append(f"... and {len(result.verify_missing_refs_after) - 200} more")
    report += [
        "",
        "Summary",
        "-" * 80,
        f"Referenced audio copied preserving folders: {result.copied_audio}",
        f"Recovered missing copied to Media:          {result.recovered_missing}",
        f"Missing not found skipped:                  {result.skipped_missing}",
        f"Ambiguous missing skipped:                  {result.skipped_ambiguous}",
        f"Unused copied to side folder:               {result.copied_unused}",
        f"Duplicate side copies:                      {result.copied_duplicates}",
        f"Relink replacements:                        {result.relink_replacements}",
        f"Broken refs after consolidate:              {result.verify_missing_after}",
        f"Errors:                                     {len(result.errors)}",
        "",
    ]
    if ambiguous:
        report += ["Skipped ambiguous missing", "-" * 80]
        report += [f"{ref}: {count} candidates with different size/hash" for ref, count in ambiguous]
        report.append("")
    if not_found:
        report += ["Skipped missing not found", "-" * 80] + [str(ref) for ref in not_found] + [""]
    if result.errors:
        report += ["Errors", "-" * 80] + result.errors + [""]

    _emit_progress(progress_callback, phase="write_reports", message="Writing reports...")
    result.report_path.write_text("\n".join(report), encoding="utf-8")
    result.relink_report_path.write_text("\n".join(relink_lines), encoding="utf-8")
    result.relink_debug_report_path.write_text("\n".join(debug_lines), encoding="utf-8")

    _emit_progress(progress_callback, phase="done", message="Consolidate complete.")
    return result
