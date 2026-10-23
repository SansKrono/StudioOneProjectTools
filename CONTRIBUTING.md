# Contributing

This project is in the beta-stable RC stage.

Preferred contribution areas:

- Studio One `.song` parsing improvements (ideally with a small anonymised sample in `tests/`).
- Crash Analyzer minidump evidence ranking.
- Safer file recovery edge cases.
- UI clarity and release packaging.

Rules:

- Never modify the original `.song` file. Cleanup/recovery features write separate copies.
- Ask for confirmation before anything that moves or adds files in the user's project folder.
- Keep the app free of network access and runtime dependencies outside the standard library.
- Only run external programs from trusted, absolute locations, and never pass untrusted text to them unquoted.
- Avoid hardcoded plugin vendor assumptions in crash analysis.
- Add or update a test in `tests/` for every fix, and run `python -m unittest discover -s tests -v` before sending a change.
