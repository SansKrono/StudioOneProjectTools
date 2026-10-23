# Studio One Project Tools v2

**Studio One Project Tools v2** is a Windows utility for cleaning up, consolidating, recovering and
diagnosing Studio One / Fender Studio Pro projects, without overwriting the original `.song` file.

It can collect referenced audio into a self-contained project folder, find and relink missing files,
spot unused and duplicate audio, write a copy of a project without bypassed FX, and analyze crash dumps
with a built-in, read-only minidump parser.

## Features

### Consolidation

- Creates a new `<Song>_CONSOLIDATED` folder; the original project is untouched.
- Copies audio inside the project with its folder structure, so references stay valid.
- Copies external audio into `External\` and recovered missing audio into `Media\`, and relinks both.
- Optionally copies unused and duplicate side files into `_Unused\` and `_Duplicates\`.
- Re-reads the new `.song` afterwards and reports any reference that is still broken.
- **Batch Consolidate** processes many projects in one run and can skip autosaves/backups.

### Missing file recovery

- Searches all indexed drives through [Everything](https://www.voidtools.com/) / `es.exe`.
- Only *safe* matches are used: one candidate, or several identical copies (same size + quick hash).
- Writes a new relinked `.song` copy and a relink report.

### Plugins

- Lists FX by track and slot, with search, filters and bypassed-FX view.
- Remove, bypass or unbypass selected FX, or drop every bypassed FX, in a new `.song` copy.

### Audio / files

- Shows referenced, external, missing, duplicate and unused audio.
- Moves unused or duplicate side files out of the project after confirmation. Files the project
  references are never moved by the duplicate cleanup.

### Crash Analyzer

- Reads `.dmp`/`.mdmp` minidumps directly: exception, module list, thread stacks.
- Finds the fault surface module and scans saved stacks for module addresses.
- Matches project FX only against stack evidence (loaded is not evidence).
- `score=` for project FX matches, `rank=` for any non-system module; the strongest are highlighted.

See [docs/MANUAL.md](docs/MANUAL.md) for details.

## Safety model

- The original `.song` is never overwritten; writing over it is refused in code.
- Edited `.song` copies are written to a temporary file first, then moved into place.
- Consolidation only copies; it never moves or deletes source files.
- Actions that change the original project folder (restoring missing files into `Media\`,
  moving files) ask for confirmation first.
- No network access, telemetry or auto-update. The only external program the app runs is `es.exe`,
  found in trusted locations only (configured absolute path, app folder, absolute `PATH` entries,
  `Program Files\Everything`), never from the current working folder.

See [AUDIT.md](AUDIT.md) for the security and behaviour review of this fork.

## Requirements

- Windows
- Python 3.11+ (to run from source; the app uses only the standard library)
- [Everything](https://www.voidtools.com/) running, plus `es.exe`, for missing-file search

## Run from source

```bash
python main.py
```

## Run the tests

```bash
python -m unittest discover -s tests -v
```

## Build the Windows EXE

```bat
BUILD_WINDOWS_EXE.bat
```

Output: `dist\StudioOneProjectTools\StudioOneProjectTools.exe`. See [WINDOWS_BUILD.md](WINDOWS_BUILD.md).

## About es.exe

`es.exe` is the third-party command-line client for Everything. It is not included in this repository.
If you distribute it with a build, check and follow its license terms.

## Status

Beta Stable RC2.
