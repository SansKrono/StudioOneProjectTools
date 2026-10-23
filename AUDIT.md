# Audit of this fork

Reviewed: upstream `ArtemBankin/StudioOneProjectTools` at commit `8c163c2` ("Initial beta stable RC1").
The fork was byte-identical to upstream at the time of review.

## Verdict

**No malicious behaviour was found.** The original code contains no network access, telemetry,
auto-update, credential/registry access, obfuscated or encoded code, `eval`/`exec`, persistence
(startup entries, scheduled tasks) or deletion of user files. The bundled documents (`.docx`, `.pdf`)
contained no macros, scripts, embedded files or external links.

The real risks were **bugs that could silently damage or mislead**, a few **hardening gaps**, and
**performance** problems. All of the items below are fixed in this fork and covered by tests in `tests/`.

## What the app does to your system

| Action | Where | When |
| --- | --- | --- |
| Reads `.song` archives and audio files | Your project folders | When you open/scan a project |
| Writes new `.song` copies, reports, consolidated folders | Where you choose | Only on your action |
| Copies recovered files into the project's `Media\` | Original project folder | **Restore missing files**, after confirmation |
| Moves audio files | A folder you choose | **Move …** actions, after confirmation |
| Runs `es.exe` (Everything CLI) | Trusted locations only | Missing-file search / Test |
| Reads crash dumps/logs (read-only) | `%LOCALAPPDATA%\CrashDumps`, Fender and PreSonus app data | Crash Analyzer tab |
| Writes crash reports | `%LOCALAPPDATA%\StudioOneProjectTools\CrashReports` | When you analyze a crash |
| Writes `settings.json`, error logs | Next to the app | On save / on error |
| Opens folders in Explorer | Folders only, never files | When you click "Open …" |

## Findings and fixes

### Correctness: could break projects or mislead

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| 1 | High | Restore missing files, Consolidate (with missing files) and Batch always crashed with `NameError: mode`. | Removed the dead instance/mode plumbing; one clear code path. |
| 2 | High | **Move duplicates** moved audio the project *references* out of the original project folder, breaking it, with no confirmation. | Never moves referenced files; confirmation added. |
| 3 | High | **Restore missing** relinked with chained `str.replace`, producing broken paths like `Old Takes\Media\file.wav`, and could also rewrite unrelated text containing the file name. | New single-pass `core/relink.py`; a file-name rule only rewrites a quoted *relative* path ending in that name. |
| 4 | High | **Consolidate** copied recovered missing files but never relinked them (bare file-name references were skipped), so the "consolidated" project still pointed at the old location. | Recovered files are relinked with the same safe rules. |
| 5 | High | The Consolidate **post-check reported success** for broken references, because analysis falls back to any same-named file anywhere in the folder. | Post-check flags references that only resolve by name, or still point outside the new folder. |
| 6 | Medium | Crash Analyzer **dropped the crashing thread's evidence** when other threads came first (global cap of 1000 hits before filtering), and so blamed the wrong plugin. | Hits are deduplicated per thread. |
| 7 | Medium | FX-to-DLL matching matched on any shared 3-letter token (e.g. "Pro-C 2" scored highly against "Pro-Q 3.vst3"). | Requires ≥75% of the FX name's tokens, including a distinctive one. |
| 8 | Medium | `.aiff` / `.wave` references were cut to `.aif` / `.wav` (regex alternation order), so they showed as MISSING. | Longest-first alternation and a boundary check. |
| 9 | Medium | An empty FX slot (`<Attributes name="FX3"/>`) made the parser drop every later FX. | Matcher terminates on self-closing blocks; empty slots are skipped. |
| 10 | Medium | **Move selected** had no confirmation, even for files the project uses. | Confirmation, with a warning for referenced files. |
| 11 | Low | Batch "ignore autosaves" matched words like *crash*, *restore*, *versions* anywhere in the **absolute** path, so it could silently skip whole libraries. | Only checks folders below the input folder. |
| 12 | Low | The Crash Analyzer data-folder setting was never saved (not in the allowed keys). | Persisted. |
| 13 | Low | Crash display text joined lines with a literal `\n`. | Fixed. |
| 14 | Low | The "Copy all referenced audio" checkbox did nothing, and the help text described a different consolidate mode. | Removed / rewritten. |

### Security hardening

| # | Finding | Fix |
| --- | --- | --- |
| S1 | `es.exe` was looked up in the **current working folder**, in `C:\Tools` (any user can create it), as a bare `es` on PATH, and via a default relative setting `"es.exe"`. A planted `es.exe` could be executed. | Lookup is limited to a configured absolute path, the app folder, absolute PATH entries and `Program Files\Everything`. |
| S2 | File names from the (untrusted) `.song` were passed to `es.exe` unquoted, so a name starting with `-` could act as an option. | Queries are always quoted; results are filtered to exact names. |
| S3 | CSV export wrote names verbatim, allowing spreadsheet formula injection (`=HYPERLINK(...)`). | Formula-like cells are prefixed with `'`. |
| S4 | `.song` copies were written in place; a crash mid-write left a corrupt file, and nothing stopped writing over the source. | Written to a temp file then moved; overwriting the source is refused. |
| S5 | Crash reports were written *into* vendor folders (`%LOCALAPPDATA%\CrashDumps\...`), twice per dump, and logs were duplicated in full. | One report per analysis, in the app's own folder. |
| S6 | Build used UPX (common antivirus false-positive trigger), upgraded the global pip, and required a third-party `es.exe` that is not in the repo. | UPX off; isolated `.venv-build`; tests run before building; `es.exe` bundled only if present. CI workflow gets `contents: read`. |
| S7 | `python-docx` was a declared dependency but never used. | Removed. The app uses only the standard library. |

### Performance

| Area | Before | After |
| --- | --- | --- |
| Crash dump analysis (40 threads × 256 KB stacks, 306 modules) | ~35 s | ~0.16 s |
| Module lookup per stack slot | Linear scan of all modules | Binary search; dump is memory-mapped instead of read fully |
| Unresolved audio references | One full `rglob` of the project per reference | One walk, name index |
| Plugin parsing | Rescanned the XML prefix per FX (quadratic) | Single pass |
| Missing-file search | Up to 6 `es.exe` launches per name, each flashing a console window | 1 launch, no console window |
| App startup | Recursively scanned `Documents\Studio One` (the song library) for "logs" | Crash folders scanned lazily on first visit to the tab; song library not scanned |
| Audio table redraw/search | Linear scan of all candidates per row | Dictionary lookup |

### Removed dead code

`core/xml_utils.py`, `models/project.py`, ~200 lines of unused cdb/WinDbg discovery (which recursively
searched Windows Kits and `WindowsApps`), duplicate helpers (`format_bytes` ×4, archive readers ×3,
`TEXT_MEMBER_SUFFIXES` ×2), and an unused second definition of `_rank_modules_from_stack_hits`.

## Not verified / known limitations

- **No real `.song` files were available.** Tests use synthetic projects built from the parser's
  assumptions (Windows-style paths in attribute values). If Studio One stores references as
  `file:///...` URLs, the analyzer still falls back to name-based resolution as before, and
  relinking of such URL references has not been validated. Test on a copy of a real project first.
- **`es.exe` was not installed here.** The Everything integration is tested with a mocked process.
  Non-ASCII file names in `es.exe` output depend on its console code page.
- Project analysis still runs on the UI thread, so very large projects freeze the window briefly.
- `es.exe` and Everything are third-party binaries outside the scope of this review.

## How to check it yourself

```bash
python -m unittest discover -s tests -v
```

The suite (25 tests) covers every numbered fix above, plus a GUI smoke test that drives the real
Tk window with dialogs stubbed out.
