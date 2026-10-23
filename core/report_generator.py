from pathlib import Path
import csv
from datetime import datetime
from core.common import format_bytes as format_size


def generate_txt_report(song_path: Path | None, plugins, audio_rows) -> str:
    total_plugins = len(plugins)
    bypassed_plugins = [p for p in plugins if p.bypassed]

    audio_counts = {}
    for item in audio_rows:
        audio_counts[item.status] = audio_counts.get(item.status, 0) + 1

    total_audio_size = sum(item.size or 0 for item in audio_rows)
    duplicate_size = sum(item.size or 0 for item in audio_rows if item.status == "DUPLICATE")
    unused_size = sum(item.size or 0 for item in audio_rows if item.status == "UNUSED")

    lines = []
    lines.append("Studio One Project Tools Report")
    lines.append("=" * 40)
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Project: {song_path if song_path else 'Not loaded'}")
    lines.append("")

    lines.append("Summary")
    lines.append("-" * 40)
    lines.append(f"Total plugins:        {total_plugins}")
    lines.append(f"Bypassed FX:          {len(bypassed_plugins)}")
    lines.append(f"Audio rows:           {len(audio_rows)}")
    lines.append(f"Normal audio:         {audio_counts.get('NORMAL', 0)}")
    lines.append(f"External audio:       {audio_counts.get('EXTERNAL', 0)}")
    lines.append(f"Duplicate audio:      {audio_counts.get('DUPLICATE', 0)}")
    lines.append(f"Unused audio:         {audio_counts.get('UNUSED', 0)}")
    lines.append(f"Missing audio:        {audio_counts.get('MISSING', 0)}")
    lines.append(f"Total audio size:     {format_size(total_audio_size)}")
    lines.append(f"Duplicate audio size: {format_size(duplicate_size)}")
    lines.append(f"Unused audio size:    {format_size(unused_size)}")
    lines.append("")

    lines.append("Bypassed FX")
    lines.append("-" * 40)
    if bypassed_plugins:
        for p in bypassed_plugins:
            lines.append(f"{p.track_name} | {p.slot_name} | {p.plugin_name}")
    else:
        lines.append("No bypassed FX found.")
    lines.append("")

    for status in ["MISSING", "EXTERNAL", "DUPLICATE", "UNUSED"]:
        rows = [a for a in audio_rows if a.status == status]
        lines.append(f"{status} audio")
        lines.append("-" * 40)
        if rows:
            for a in rows:
                path = str(a.path) if a.path else (a.reference or "")
                lines.append(f"{format_size(a.size):>12} | {a.filename} | {path}")
        else:
            lines.append(f"No {status.lower()} audio found.")
        lines.append("")

    return "\n".join(lines)


def export_txt_report(output_path: Path, song_path: Path | None, plugins, audio_rows) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generate_txt_report(song_path, plugins, audio_rows), encoding="utf-8")
    return output_path


def _csv_cell(value):
    """Prefix text that a spreadsheet would run as a formula (CSV injection)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_csv_report(output_path: Path, song_path: Path | None, plugins, audio_rows) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [
        ["Studio One Project Tools Report"],
        ["Project", str(song_path) if song_path else "Not loaded"],
        [],
        ["Section", "Track/Status", "Slot/Size", "Name/File", "Path/Reference"],
    ]
    rows += [
        ["PLUGIN", p.track_name, p.slot_name, p.plugin_name, "BYPASSED" if p.bypassed else "ACTIVE"]
        for p in plugins
    ]
    rows += [
        ["AUDIO", a.status, a.size if a.size is not None else "", a.filename, str(a.path) if a.path else (a.reference or "")]
        for a in audio_rows
    ]

    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows([_csv_cell(v) for v in row] for row in rows)

    return output_path
