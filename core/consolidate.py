from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import zipfile
from urllib.parse import quote, unquote

from core.audio_manager import quick_audio_fingerprint, build_audio_table_rows
from core.plugin_manager import AUDIO_MIXER_XML, parse_fx_blocks, modify_fx_xml_by_indices


TEXT_MEMBER_SUFFIXES = (
    ".xml",
    ".txt",
    ".settings",
    ".songdata",
    ".json",
)


@dataclass
class ConsolidateResult:
    output_dir: Path
    output_song: Path
    media_dir: Path
    report_path: Path
    relink_report_path: Path
    relink_debug_report_path: Path | None = None
    copied_audio: int = 0
    copied_unused: int = 0
    copied_duplicates: int = 0
    recovered_missing: int = 0
    skipped_missing: int = 0
    skipped_ambiguous: int = 0
    removed_bypassed_fx: int = 0
    relink_replacements: int = 0
    verify_missing_after: int = 0
    verify_total_refs_after: int = 0
    verify_missing_refs_after: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def ensure_unique_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest

    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent

    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def ensure_unique_folder(dest: Path) -> Path:
    if not dest.exists():
        return dest

    parent = dest.parent
    name = dest.name
    i = 1
    while True:
        candidate = parent / f"{name}_{i}"
        if not candidate.exists():
            return candidate
        i += 1


def copy_file_to_media(src: Path, media_dir: Path) -> Path:
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = ensure_unique_destination(media_dir / src.name)
    shutil.copy2(src, dest)
    return dest


def copy_file_to_folder(src: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = ensure_unique_destination(target_dir / src.name)
    shutil.copy2(src, dest)
    return dest


def move_file_to_folder(src: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = ensure_unique_destination(target_dir / src.name)
    shutil.move(str(src), str(dest))
    return dest


def format_bytes(num: int | float | None) -> str:
    if num is None:
        return "0 B"
    num = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{num:.0f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _emit_progress(progress_callback, **payload):
    if progress_callback:
        try:
            progress_callback(payload)
        except Exception:
            pass


def copy_file_with_progress(src: Path, dest: Path, progress_callback=None, chunk_size: int = 8 * 1024 * 1024) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = src.stat().st_size if src.exists() else 0
    copied = 0

    _emit_progress(
        progress_callback,
        phase="copy_file_start",
        current_file=src.name,
        current_source=str(src),
        current_dest=str(dest),
        current_total_bytes=total,
        current_copied_bytes=0,
    )

    with src.open("rb") as fsrc, dest.open("wb") as fdst:
        while True:
            chunk = fsrc.read(chunk_size)
            if not chunk:
                break
            fdst.write(chunk)
            copied += len(chunk)
            _emit_progress(
                progress_callback,
                phase="copy_file_progress",
                current_file=src.name,
                current_source=str(src),
                current_dest=str(dest),
                current_total_bytes=total,
                current_copied_bytes=copied,
                copied_bytes_delta=len(chunk),
            )

    try:
        shutil.copystat(src, dest)
    except Exception:
        pass

    _emit_progress(
        progress_callback,
        phase="copy_file_done",
        current_file=src.name,
        current_source=str(src),
        current_dest=str(dest),
        current_total_bytes=total,
        current_copied_bytes=total,
    )
    return dest


def copy_file_to_media_progress(src: Path, media_dir: Path, progress_callback=None) -> Path:
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = ensure_unique_destination(media_dir / src.name)
    return copy_file_with_progress(src, dest, progress_callback=progress_callback)


def copy_file_to_folder_progress(src: Path, target_dir: Path, progress_callback=None) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = ensure_unique_destination(target_dir / src.name)
    return copy_file_with_progress(src, dest, progress_callback=progress_callback)


def filename_from_reference(ref: str) -> str:
    return Path(ref.replace("\\", "/")).name


def _studio_one_ref(dest: Path, output_dir: Path, mode: str = "absolute") -> str:
    """
    Absolute path is the safest relink format for Studio One consolidation.
    Relative Media\\file works in many cases, but some projects keep absolute
    media references internally and fail to resolve relative replacements.
    """
    if mode == "relative":
        try:
            rel = dest.resolve().relative_to(output_dir.resolve())
            return str(rel).replace("/", "\\")
        except Exception:
            pass
    return str(dest.resolve()).replace("/", "\\")


def _path_variants(value: str) -> set[str]:
    variants = set()
    if not value:
        return variants

    raw = str(value)
    variants.add(raw)
    variants.add(raw.replace("\\", "/"))
    variants.add(raw.replace("/", "\\"))

    try:
        unq = unquote(raw)
        variants.add(unq)
        variants.add(unq.replace("\\", "/"))
        variants.add(unq.replace("/", "\\"))
    except Exception:
        pass

    for v in list(variants):
        try:
            variants.add(quote(v, safe="/:\\._-()[]{} @#$%&+=,;~!"))
        except Exception:
            pass

    # XML sometimes stores backslashes escaped/doubled in serialized blobs.
    for v in list(variants):
        variants.add(v.replace("\\", "\\\\"))
        variants.add(v.replace("/", "\\/"))

    return {v for v in variants if v}


def _new_ref_variants(dest: Path, output_dir: Path) -> list[str]:
    abs_ref = str(dest.resolve()).replace("/", "\\")
    abs_slash = abs_ref.replace("\\", "/")
    rel_ref = _studio_one_ref(dest, output_dir, mode="relative")
    rel_slash = rel_ref.replace("\\", "/")
    filename = dest.name

    values = [abs_ref, abs_slash, rel_ref, rel_slash, filename]
    unique = []
    seen = set()
    for v in values:
        if v and v not in seen:
            unique.append(v)
            seen.add(v)
    return unique


def _add_replacement_variants(replacements: dict[str, str], old_ref: str, dest: Path, output_dir: Path, *, prefer_absolute: bool = True, allow_bare_filename: bool = False):
    """
    Build old->new replacement map.

    Default is strict:
    - full old path/reference variants only;
    - no bare filename replacement.

    Bare filename replacement is dangerous for Studio One because it can create:
      OldFolder\\Media\\ + NewAbsolutePath
    """
    if not old_ref:
        return

    new_values = _new_ref_variants(dest, output_dir)
    primary_new = new_values[0] if prefer_absolute else new_values[2 if len(new_values) > 2 else 0]
    primary_new_slash = primary_new.replace("\\", "/")

    raw_norm = str(old_ref).replace("\\", "/")
    is_bare_filename = "/" not in raw_norm and ":" not in raw_norm

    # If old_ref is only "file.wav", normally skip it.
    # It cannot safely be replaced by an absolute path.
    if is_bare_filename and not allow_bare_filename:
        return

    old_variants = _path_variants(old_ref)

    for old in old_variants:
        replacements[old] = primary_new_slash if "/" in old and "\\" not in old else primary_new


def _add_source_path_replacements(replacements: dict[str, str], source_path: Path, dest: Path, output_dir: Path):
    """
    Add replacements based on the actual resolved old file path.
    This is the key for projects where regex extracted only filename,
    while Studio One still stores full absolute path elsewhere.
    """
    try:
        source_abs = str(source_path.resolve()).replace("/", "\\")
    except Exception:
        source_abs = str(source_path).replace("/", "\\")
    _add_replacement_variants(replacements, source_abs, dest, output_dir, prefer_absolute=True, allow_bare_filename=False)



def _safe_missing_candidates_for_audio_rows(audio_rows, missing_candidates) -> tuple[list, list[tuple[str, int]], list[str]]:
    """
    Returns:
      safe_candidates, ambiguous_rows, not_found_refs

    Safe means:
      - exactly one candidate, or
      - multiple candidates but all share identical size + quick hash.
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

        groups = {}
        for c in candidates:
            key = (c.size, c.quick_hash)
            groups.setdefault(key, []).append(c)

        valid_groups = [g for key, g in groups.items() if key[0] is not None and key[1]]
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(valid_groups) == 1 and len(valid_groups[0]) == len(candidates):
            chosen = valid_groups[0][0]
        else:
            ambiguous.append((ref, len(candidates)))
            continue

        if chosen.missing_reference not in seen_refs:
            safe.append(chosen)
            seen_refs.add(chosen.missing_reference)

    return safe, ambiguous, not_found


def _destination_for_source(src: Path, project_dir: Path, output_dir: Path, media_folder_name: str = "Media") -> Path:
    """
    Preserve project-relative folder structure.

    Examples:
      Project\\Media\\a.wav   -> Consolidated\\Media\\a.wav
      Project\\Bounces\\b.wav -> Consolidated\\Bounces\\b.wav
      External\\x.wav         -> Consolidated\\External\\x.wav
    """
    try:
        rel = src.resolve().relative_to(project_dir.resolve())
        return output_dir / rel
    except Exception:
        return output_dir / "External" / src.name


def _ref_for_destination(dest: Path, output_dir: Path, prefer_relative: bool = True) -> str:
    if prefer_relative:
        try:
            rel = dest.resolve().relative_to(output_dir.resolve())
            return str(rel).replace("/", "\\")
        except Exception:
            pass
    return str(dest.resolve()).replace("/", "\\")



def _read_archive_texts(song_path: Path) -> list[str]:
    texts = []
    with zipfile.ZipFile(song_path, "r") as z:
        for name in z.namelist():
            if name.lower().endswith(TEXT_MEMBER_SUFFIXES):
                try:
                    texts.append(z.read(name).decode("utf-8", errors="replace"))
                except Exception:
                    pass
    return texts


def verify_consolidated_song(output_song: Path, output_dir: Path) -> tuple[int, int, list[str]]:
    """
    Re-analyze the created consolidated .song against its new output folder.
    Returns:
      total audio refs, missing count, missing refs
    """
    try:
        texts = _read_archive_texts(output_song)
        rows = build_audio_table_rows(output_dir, texts)
        refs = [r for r in rows if r.reference]
        missing = [r.reference or r.filename for r in rows if r.status == "MISSING"]
        return len(refs), len(missing), missing
    except Exception as e:
        return 0, 1, [f"VERIFY FAILED: {e}"]


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
    remove_bypassed_fx: bool = False,
    progress_callback=None,
) -> ConsolidateResult:
    """
    SAFE PRESERVE-FOLDERS CONSOLIDATE.

    Main rule:
    - copy .song byte-for-byte first;
    - preserve project-relative audio folders;
    - do NOT collapse Bounces/Media/other folders into one Media folder;
    - only external/missing recovered files need relink into External/Media;
    - original project is never modified.
    """
    output_parent = Path(output_parent)
    output_dir = ensure_unique_folder(output_parent / f"{song_path.stem}_CONSOLIDATED")
    media_dir = output_dir / media_folder_name
    duplicates_dir = output_dir / duplicates_folder_name
    unused_dir = output_dir / unused_folder_name
    output_song = output_dir / f"{song_path.stem}_CONSOLIDATED.song"
    report_path = output_dir / "consolidate_report.txt"
    relink_report_path = output_dir / "relink_report.txt"
    relink_debug_report_path = output_dir / "relink_debug_report.txt"

    output_dir.mkdir(parents=True, exist_ok=False)
    media_dir.mkdir(parents=True, exist_ok=True)

    result = ConsolidateResult(
        output_dir=output_dir,
        output_song=output_song,
        media_dir=media_dir,
        report_path=report_path,
        relink_report_path=relink_report_path,
        relink_debug_report_path=relink_debug_report_path,
    )

    consolidate_lines = [
        "Studio One Project Tools - Consolidate Report",
        "=" * 80,
        f"Source song:       {song_path}",
        f"Source folder:     {project_dir}",
        f"Output folder:     {output_dir}",
        f"Output song:       {output_song}",
        "",
        "Mode: SAFE PRESERVE FOLDERS.",
        "Main .song is copied byte-for-byte first.",
        "Project-relative audio keeps original relative folder paths.",
        "Original project was not modified.",
        "",
    ]
    relink_lines = [
        "Studio One Project Tools - Relink Report",
        "=" * 80,
        "Preserve-folders mode: internal project-relative files usually do not need relink.",
        "Only external/recovered files are relinked if needed.",
        "",
    ]
    relink_debug_lines = [
        "Studio One Project Tools - Relink Debug Report",
        "=" * 80,
        "Preserve-folders mode.",
        "",
    ]

    # Copy .song exactly first.
    _emit_progress(progress_callback, phase="write_song", message="Copying .song byte-for-byte...")
    shutil.copy2(song_path, output_song)

    # Build copy plan for referenced existing audio.
    referenced_plan = []
    seen_refs = set()
    for row in audio_rows:
        if row.status == "MISSING":
            continue
        if not row.reference or not row.path or not row.path.exists():
            continue
        key = str(row.path.resolve()).lower()
        if key in seen_refs:
            continue
        seen_refs.add(key)
        referenced_plan.append(row)

    total_copy_bytes = 0
    for row in referenced_plan:
        try:
            total_copy_bytes += row.path.stat().st_size
        except Exception:
            pass

    _emit_progress(
        progress_callback,
        phase="plan",
        total_files=len(referenced_plan),
        total_bytes=total_copy_bytes,
        message=f"Copy plan: {len(referenced_plan)} files / {format_bytes(total_copy_bytes)}",
    )

    replacements: dict[str, str] = {}

    # Copy referenced files preserving project-relative folders.
    for row in referenced_plan:
        try:
            src = row.path.resolve()
            dest = _destination_for_source(src, project_dir, output_dir, media_folder_name=media_folder_name)
            dest = ensure_unique_destination(dest)

            _emit_progress(
                progress_callback,
                phase="copy_referenced",
                file_index=result.copied_audio + 1,
                total_files=len(referenced_plan),
                copied_files=result.copied_audio,
                total_bytes=total_copy_bytes,
                message=f"Copying {result.copied_audio + 1}/{len(referenced_plan)}: {src.name}",
            )
            copy_file_with_progress(src, dest, progress_callback=progress_callback)
            result.copied_audio += 1

            try:
                src.resolve().relative_to(project_dir.resolve())
                is_external = False
            except Exception:
                is_external = True

            if is_external:
                # Only external files need relink into the new project folder.
                new_ref = _ref_for_destination(dest, output_dir, prefer_relative=True)
                _add_source_path_replacements(replacements, src, dest, output_dir)
                _add_replacement_variants(replacements, row.reference, dest, output_dir, prefer_absolute=False, allow_bare_filename=False)

                relink_lines.append("-" * 80)
                relink_lines.append(f"External source: {src}")
                relink_lines.append(f"Copied to:       {dest}")
                relink_lines.append(f"New ref:         {new_ref}")

            relink_debug_lines.append("-" * 80)
            relink_debug_lines.append(f"REFERENCED: {row.filename}")
            relink_debug_lines.append(f"Old reference: {row.reference}")
            relink_debug_lines.append(f"Copied source: {src}")
            relink_debug_lines.append(f"Copied dest:   {dest}")
            relink_debug_lines.append(f"Relink needed: {'yes' if is_external else 'no'}")

        except Exception as e:
            result.errors.append(f"Copy referenced audio failed: {row.filename}: {e}")

    # Recover missing safe candidates into Media and relink.
    safe_missing = []
    ambiguous = []
    not_found = []
    if recover_missing:
        safe_missing, ambiguous, not_found = _safe_missing_candidates_for_audio_rows(audio_rows, missing_candidates or [])
        result.skipped_ambiguous = len(ambiguous)
        result.skipped_missing = len(not_found)

        for c in safe_missing:
            try:
                dest = ensure_unique_destination(media_dir / c.filename)
                _emit_progress(
                    progress_callback,
                    phase="recover_missing",
                    file_index=result.recovered_missing + 1,
                    total_files=len(safe_missing),
                    message=f"Recovering missing {result.recovered_missing + 1}/{len(safe_missing)}: {c.filename}",
                )
                copy_file_with_progress(c.source_path, dest, progress_callback=progress_callback)
                result.recovered_missing += 1

                _add_replacement_variants(replacements, c.missing_reference, dest, output_dir, prefer_absolute=False, allow_bare_filename=False)
                _add_source_path_replacements(replacements, c.source_path, dest, output_dir)

                relink_lines.append("-" * 80)
                relink_lines.append(f"Recovered missing: {c.missing_reference}")
                relink_lines.append(f"Found source:      {c.source_path}")
                relink_lines.append(f"Copied to:         {dest}")

            except Exception as e:
                result.errors.append(f"Recover missing failed: {c.filename}: {e}")

    # Optional side copies preserving info only, does not affect .song.
    for row in audio_rows:
        if not row.path or not row.path.exists():
            continue
        if row.status == "UNUSED" and copy_unused:
            try:
                _emit_progress(progress_callback, phase="copy_unused", message=f"Copying unused: {row.filename}")
                copy_file_to_folder_progress(row.path, unused_dir, progress_callback=progress_callback)
                result.copied_unused += 1
            except Exception as e:
                result.errors.append(f"Copy unused failed: {row.filename}: {e}")

        if row.status == "DUPLICATE" and copy_duplicate_nonreferenced and not row.reference:
            try:
                _emit_progress(progress_callback, phase="copy_duplicate", message=f"Copying duplicate side copy: {row.filename}")
                copy_file_to_folder_progress(row.path, duplicates_dir, progress_callback=progress_callback)
                result.copied_duplicates += 1
            except Exception as e:
                result.errors.append(f"Copy duplicate failed: {row.filename}: {e}")

    # Apply relink only if needed for external/missing. Otherwise leave .song byte-identical.
    if replacements:
        _emit_progress(progress_callback, phase="write_song", message="Applying limited relink for external/recovered files...")
        tmp_song = output_song.with_suffix(".tmp.song")
        with zipfile.ZipFile(output_song, "r") as zin, zipfile.ZipFile(tmp_song, "w", compression=zipfile.ZIP_DEFLATED) as zout:
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
                    for old, new in sorted(replacements.items(), key=lambda kv: len(kv[0]), reverse=True):
                        if old:
                            count = new_text.count(old)
                            if count:
                                result.relink_replacements += count
                                relink_debug_lines.append(f"HIT {info.filename}: {count} x {old!r} -> {new!r}")
                                new_text = new_text.replace(old, new)

                    if new_text != text:
                        zout.writestr(info, new_text.encode("utf-8"))
                    else:
                        zout.writestr(info, data)
                else:
                    zout.writestr(info, data)
        tmp_song.replace(output_song)

    # Bypassed FX removal disabled in main consolidate.
    result.removed_bypassed_fx = 0

    # Post-check: re-analyze the newly created project in its output folder.
    _emit_progress(progress_callback, phase="verify", message="Verifying consolidated .song...")
    total_refs_after, missing_after, missing_refs_after = verify_consolidated_song(output_song, output_dir)
    result.verify_total_refs_after = total_refs_after
    result.verify_missing_after = missing_after
    result.verify_missing_refs_after = missing_refs_after

    consolidate_lines.append("Post-check")
    consolidate_lines.append("-" * 80)
    consolidate_lines.append(f"Audio refs in consolidated .song: {result.verify_total_refs_after}")
    consolidate_lines.append(f"Missing refs after consolidate:   {result.verify_missing_after}")
    if result.verify_missing_refs_after:
        consolidate_lines.append("")
        consolidate_lines.append("Missing refs after consolidate:")
        for ref in result.verify_missing_refs_after[:200]:
            consolidate_lines.append(str(ref))
        if len(result.verify_missing_refs_after) > 200:
            consolidate_lines.append(f"... and {len(result.verify_missing_refs_after) - 200} more")
    consolidate_lines.append("")

    consolidate_lines.append("Summary")
    consolidate_lines.append("-" * 80)
    consolidate_lines.append(f"Referenced audio copied preserving folders: {result.copied_audio}")
    consolidate_lines.append(f"Recovered missing copied to Media:          {result.recovered_missing}")
    consolidate_lines.append(f"Missing not found skipped:                  {result.skipped_missing}")
    consolidate_lines.append(f"Ambiguous missing skipped:                  {result.skipped_ambiguous}")
    consolidate_lines.append(f"Unused copied to side folder:               {result.copied_unused}")
    consolidate_lines.append(f"Duplicate side copies:                      {result.copied_duplicates}")
    consolidate_lines.append(f"Removed bypassed FX:                        {result.removed_bypassed_fx} (disabled in main safe consolidate)")
    consolidate_lines.append(f"Relink replacement hits:                    {result.relink_replacements}")
    consolidate_lines.append(f"Post-check missing after consolidate:       {result.verify_missing_after}")
    consolidate_lines.append(f"Errors:                                     {len(result.errors)}")
    consolidate_lines.append("")

    if ambiguous:
        consolidate_lines.append("Skipped ambiguous missing")
        consolidate_lines.append("-" * 80)
        for ref, count in ambiguous:
            consolidate_lines.append(f"{ref}: {count} candidates with different size/hash")
        consolidate_lines.append("")

    if not_found:
        consolidate_lines.append("Skipped missing not found")
        consolidate_lines.append("-" * 80)
        for ref in not_found:
            consolidate_lines.append(str(ref))
        consolidate_lines.append("")

    if result.errors:
        consolidate_lines.append("Errors")
        consolidate_lines.append("-" * 80)
        consolidate_lines.extend(result.errors)
        consolidate_lines.append("")

    _emit_progress(progress_callback, phase="write_reports", message="Writing reports...")
    report_path.write_text("\n".join(consolidate_lines), encoding="utf-8")
    relink_report_path.write_text("\n".join(relink_lines), encoding="utf-8")
    relink_debug_report_path.write_text("\n".join(relink_debug_lines), encoding="utf-8")

    _emit_progress(progress_callback, phase="done", message="Consolidate complete.")
    return result
