# Release Notes

## v2 Beta Stable RC2 (audited fork)

### Fixes

- Restore missing files / Consolidate / Batch: fixed a crash (`NameError: mode`) whenever missing files existed.
- Restore missing files: relinking no longer produces broken paths such as `Old\Media\file.wav`.
- Consolidate: recovered missing files are now actually relinked in the new `.song`.
- Consolidate post-check now detects references that only resolved because a same-named file existed elsewhere.
- Audio analysis: `.aiff` and `.wave` references are no longer cut to `.aif` / `.wav`.
- Audio analysis: relative references keep their folder (`Media\kick.wav`, not just `kick.wav`).
- Plugins: an empty FX slot no longer hides every FX after it.
- Crash Analyzer: evidence from the crashing thread is no longer dropped when other threads come first.
- Crash Analyzer: stricter FX name matching (e.g. "Pro-C 2" no longer matches "Pro-Q 3.vst3").
- Move duplicates: never moves audio that the project references; asks for confirmation.
- Move selected: asks for confirmation and warns when referenced files are selected.
- Batch: the autosave filter only looks at folders below the input folder.
- The Crash Analyzer data folder setting is now saved.

### Security / trust

- `es.exe` is never run from the current working folder, `C:\Tools` or a bare `es` on PATH.
- Everything queries are always quoted, so file names from a `.song` cannot act as `es.exe` options.
- `es.exe` runs without flashing a console window.
- CSV reports neutralise spreadsheet formulas in file/plugin names.
- Writing over the original `.song` is refused in code; copies are written atomically.
- Crash reports go to `%LOCALAPPDATA%\StudioOneProjectTools\CrashReports` instead of vendor folders.
- Removed unused cdb/WinDbg discovery code, unused modules and the unused `python-docx` dependency.
- Build: UPX disabled, isolated build venv, tests run before building, `es.exe` bundled only if present.

### Performance

- Crash dump analysis is ~200x faster (binary-search module lookup, memory-mapped dump).
- Project audio analysis walks the project folder once instead of once per unresolved reference.
- Plugin parsing is linear instead of quadratic in the mixer XML size.
- Crash folders are scanned when the Crash Analyzer tab is first opened, not at startup, and the
  user's song library in Documents is no longer scanned.
- Missing-file search starts one `es.exe` process per file name instead of up to six.

### Docs

- All documentation is English only; the manual is now plain Markdown (`docs/MANUAL.md`).

## v2 Beta Stable RC1

### Highlights

- Clean autonomous project consolidation.
- Missing audio search through Everything / es.exe.
- Relinked `.song` copy creation.
- Safe no-bypassed-FX project copy.
- Audio/file analysis: referenced, missing, duplicate, unused.
- Batch consolidate.
- Crash Analyzer without cdb/WinDbg.
- Minidump parser, stack memory scan, score/rank evidence.
- Clear Everything test result.
- Standard compatible ES mode only; no confusing 1.5b/manual instance UI.

### Crash Analyzer

Finalized:

- built-in minidump reader;
- no cdb dependency;
- fault surface module detection;
- stack module ranking;
- project FX matching only through stack evidence;
- red highlighting for max `score=` and max `rank=`.

### Everything / es.exe

Finalized:

- bundled/local `es.exe` auto-detect;
- no mode switch;
- standard compatible interface only;
- neutral test command;
- clear success/failure message for users.

### Known limitations

- Windows EXE must be built on Windows.
- Public repository does not include third-party `es.exe` unless redistribution is confirmed.
- Crash Analyzer is evidence-based, not a formal debugger replacement.
