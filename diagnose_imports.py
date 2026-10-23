"""Import every app module and report which ones fail. Writes diagnose_imports_log.txt."""

from pathlib import Path
import traceback

modules = [
    "core.common",
    "core.song_archive",
    "core.relink",
    "core.plugin_manager",
    "core.audio_manager",
    "core.everything",
    "core.missing_files",
    "core.consolidate",
    "core.report_generator",
    "core.settings_manager",
    "core.minidump_reader",
    "core.crash_analyzer",
    "gui.app",
]

log = []
ok = True

for m in modules:
    try:
        __import__(m)
        log.append(f"OK   {m}")
    except Exception:
        ok = False
        log.append(f"FAIL {m}")
        log.append(traceback.format_exc())

text = "\n".join(log)
Path(__file__).resolve().with_name("diagnose_imports_log.txt").write_text(text, encoding="utf-8")
print(text)

if not ok:
    raise SystemExit(1)
