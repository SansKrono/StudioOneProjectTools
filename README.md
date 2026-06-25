# Studio One Project Tools v2

**Studio One Project Tools v2** is a beta-stable utility for safe cleanup, consolidation, recovery and crash diagnostics of Studio One / Fender Studio Pro projects.

## Core idea

The tool helps create a clean autonomous copy of a Studio One project without overwriting the original `.song` file.

It can collect referenced audio, recover missing files, identify unused and duplicate side files, create safe cleaned project copies, and analyze crash dumps without WinDbg or cdb.

## Highlights

- Clean autonomous project copy.
- Fast missing audio search across connected drives through Everything / `es.exe`.
- Relinked `.song` copy creation.
- Safe no-bypassed-FX project copy.
- Plugin scanning by tracks and FX slots.
- Detection of unused files and duplicate side files.
- Batch consolidate for multiple projects.
- Built-in Crash Analyzer without WinDbg/cdb.
- Minidump parser, stack memory scan, score/rank evidence.
- Clear Everything test result.

## Features

### Project cleanup and consolidation

- Create a clean autonomous project copy.
- Copy referenced audio into a new project folder.
- Recover missing audio into a new relinked `.song` copy.
- Keep the original project untouched.
- Copy unused and duplicate side files into separate folders.
- Prepare projects for transfer, backup, archiving or delivery.

### Missing file recovery

- Uses Everything / `es.exe` for fast search across connected drives.
- `es.exe` is detected automatically when bundled next to the app.
- Test button checks the standard compatible Everything interface.
- Test is neutral and does not depend on WAV/SONG files existing in the user index.

### Plugin tools

- Parse plugins by track and FX slot.
- Detect bypassed FX.
- Create a separate no-bypassed-FX `.song` copy.
- Keep original `.song` safe.

### Audio / file analysis

- Referenced files.
- Missing files.
- Duplicate candidates.
- Unused side files.
- Project-side files that are not referenced by the `.song`.

### Batch consolidate

- Scan a folder with multiple `.song` projects.
- Ignore autosaves/backups.
- Consolidate multiple projects one by one.
- Show progress and reports.

### Crash Analyzer

Built-in crash analysis without WinDbg/cdb:

- Reads Studio One / Fender Studio Pro minidumps.
- Detects exception code and fault surface module.
- Scans stack memory for module evidence.
- Matches project FX only through stack evidence.
- Uses `score=` for project FX matches.
- Uses `rank=` for generic non-system modules.
- Highlights strongest score/rank evidence.

## Safety model

The tool is designed around safe copies:

- original `.song` is not overwritten;
- cleanup creates separate `.song` copies;
- recovery creates relinked copies;
- consolidation creates a new autonomous folder;
- reports are written separately.

## Requirements

Development:

```bash
python 3.11+
pip install -r requirements.txt
```

Runtime:

- Windows
- Studio One / Fender Studio Pro projects
- Everything installed and running for missing-file search
- optional `es.exe` placed next to the app or selected in Settings

## Run from source

```bash
python main.py
```

## Build Windows EXE

On Windows:

```bat
BUILD_WINDOWS_EXE.bat
```

Output:

```text
dist\StudioOneProjectTools\StudioOneProjectTools.exe
```

For beta-stable, the onedir build is preferred because `es.exe` can stay next to the main EXE and be detected reliably.

## Documentation

- `docs/MANUAL_RU_EN.pdf`
- `docs/MANUAL_RU_EN.docx`
- `docs/PROMO_RU_EN.txt`

## Notes about es.exe

`es.exe` is a third-party Everything command-line tool. This source repository does not include it by default. If you want to distribute a bundled release, verify and follow the third-party license terms.

## Status

Beta Stable RC1.
