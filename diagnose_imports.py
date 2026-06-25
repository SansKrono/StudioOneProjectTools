from pathlib import Path
import traceback

modules = [
    "core.song_archive",
    "core.plugin_manager",
    "core.audio_manager",
    "core.consolidate",
    "core.report_generator",
    "core.missing_files",
    "core.everything",
    "core.settings_manager",
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
Path("diagnose_imports_log.txt").write_text(text, encoding="utf-8")
print(text)

if not ok:
    raise SystemExit(1)
