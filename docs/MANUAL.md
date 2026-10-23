# Studio One Project Tools v2 – Manual

Beta Stable RC2

## Quick start

1. Extract the release archive into its own folder.
2. Start `StudioOneProjectTools.exe` (or `python main.py` from source).
3. Open a project with **Open .song**.
4. Review the **Plugins**, **Audio / Files**, **Consolidate**, **Batch Consolidate** and **Crash Analyzer** tabs.

> The original `.song` file is never overwritten. Cleanup and recovery actions write new `.song` copies
> and separate reports.
>
> Two actions do change the original project folder, and both ask first:
> **Restore missing files** copies the found files into the project's `Media` folder, and the
> **Move …** actions move audio files to a folder you choose.

## Tabs

| Tab | Purpose |
| --- | --- |
| Dashboard | Overview of the opened project and a simple health score. |
| Plugins | FX by track and slot, search, filters, bypassed FX handling. |
| Audio / Files | Referenced, missing, external, duplicate and unused audio. |
| Consolidate | Create a self-contained copy of the project folder. |
| Batch Consolidate | Consolidate many `.song` projects in one run. |
| Crash Analyzer | Read Studio One / Fender Studio Pro crash dumps and logs. |
| Settings | `es.exe` path, Everything test, names of the Media/_Duplicates/_Unused folders. |

## Everything / es.exe

Missing-file search uses [Everything](https://www.voidtools.com/) through its command-line client `es.exe`.

- `es.exe` is found automatically next to the app, on `PATH` or in `Program Files\Everything`,
  or you can select it in **Settings**. It is never taken from the current working folder.
- Everything itself must be running.
- **Test** checks that `es.exe` can talk to Everything. It does not depend on which files you have.
- Full diagnostics are saved to `everything_diagnostics.txt` next to the app.

## Plugins

- Lists plugins per track and FX slot and shows which ones are bypassed.
- Right-click selected FX to **remove**, **bypass** or **unbypass** them. A new `.song` copy is written.
- **Save copy without bypassed FX** writes a copy with every bypassed FX removed.

## Audio / Files

| Status | Meaning |
| --- | --- |
| NORMAL | Referenced by the project and found inside the project folder. |
| EXTERNAL | Referenced by the project but stored outside the project folder. |
| MISSING | Referenced by the project but not found. |
| DUPLICATE | Same size and quick hash as another file in the project. |
| UNUSED | In the project folder but not referenced by the `.song`. |

- **Restore missing files** searches Everything, copies safe matches into the project's `Media` folder
  and writes a new relinked `.song`. A match is *safe* when there is exactly one candidate, or when all
  candidates have the same size and quick hash. Ambiguous matches are skipped and listed.
- **Move duplicates** only moves duplicate side files that the project does not reference.
- **Move selected** warns you if any selected file is used by the project.

## Consolidate

Creates a new `<Song>_CONSOLIDATED` folder; the original project is not modified.

- The `.song` is copied byte-for-byte first.
- Audio inside the project is copied with its folder structure, so its references stay valid.
- Audio from outside the project is copied to `External\` and relinked.
- Missing audio with a safe match is copied to `Media\` and relinked.
- Optionally, unused files are copied to `_Unused\` and unreferenced duplicates to `_Duplicates\`.
- Optionally, a second `.song` without bypassed FX is written next to the main one.
- A post-check re-reads the new `.song` and lists any reference that is still broken.

Reports: `consolidate_report.txt`, `relink_report.txt`, `relink_debug_report.txt`.

## Batch Consolidate

- Scans a folder (optionally recursively) for `.song` projects.
- Can skip autosave/backup/history projects, judged by the project file name and by the folders
  *below* the chosen input folder.
- Consolidates the projects one by one with the same engine and shows progress.

## Crash Analyzer

- Reads minidumps (`.dmp`/`.mdmp`) with a built-in, read-only parser. No debugger, symbols or
  internet connection is used.
- Auto mode scans `%LOCALAPPDATA%\CrashDumps`, `%APPDATA%\Fender`, `%LOCALAPPDATA%\Fender` and the
  PreSonus / Studio One application-data folders. Or choose one folder manually.
- Finds the **fault surface module** from the exception address.
- Scans saved thread stacks for addresses inside loaded modules.
- Matches project FX names only against **stack evidence**; a plugin that is merely loaded is not
  treated as a suspect.
- `score=` rates project FX matches, `rank=` rates any non-system module (plugins, drivers, licensing,
  graphics, host). The highest values are highlighted in red.
- Reports are saved to `%LOCALAPPDATA%\StudioOneProjectTools\CrashReports`.

> `KERNELBASE.dll` or `ucrtbase.dll` is often only the *surface* of a crash, not its cause.
> Look at **Project FX matched in EXCEPTION THREAD stack** and **Top Exception Thread Non-System Modules**.

For the best plugin matching, open the `.song` that crashed before analyzing its dump.

## Safety rules

- Keep backups of important projects.
- Review the generated reports before deleting anything by hand.
- Open the consolidated project in Studio One and check it before removing the original.

## Files the app writes

| File | Location |
| --- | --- |
| `settings.json` | Next to the app |
| `error_log.txt`, `batch_error_log.txt`, `startup_error_log.txt` | Next to the app |
| `everything_diagnostics.txt` | Next to the app (after **Test**) |
| Crash reports | `%LOCALAPPDATA%\StudioOneProjectTools\CrashReports` |
| `.song` copies, reports, consolidated folders | Where you choose |
