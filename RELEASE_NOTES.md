# Release Notes

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
