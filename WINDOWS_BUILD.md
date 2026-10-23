# Studio One Project Tools v2 – Windows build

## Build the EXE

1. Install Python 3.11 or newer and enable **Add Python to PATH**.
2. Optional: put `es.exe` (the [Everything](https://www.voidtools.com/) command-line client) in the
   project root if you want it bundled next to the EXE. It is not part of this repository.
3. Run:

   ```bat
   BUILD_WINDOWS_EXE.bat
   ```

   The script creates a private virtual environment in `.venv-build`, installs PyInstaller from
   `requirements.txt`, runs the test suite and only then builds.

4. The application is created here:

   ```text
   dist\StudioOneProjectTools\StudioOneProjectTools.exe
   ```

If `es.exe` was not bundled, the app looks for it on `PATH` and in `Program Files\Everything`,
or you can pick it in **Settings**. It is never taken from the current working folder.

## Create a release ZIP

After a successful build, run:

```bat
MAKE_WINDOWS_RELEASE_ZIP.bat
```

It packages the app folder with the manual, README, license and this file.

## Why onedir instead of one-file

The onedir build starts faster, keeps an optional `es.exe` next to the EXE, and is less likely to be
flagged by antivirus software than a self-extracting one-file EXE. UPX compression is disabled for the
same reason.

## Publishing a release on GitHub

1. Push your changes to your repository.
2. Go to **Releases → Draft a new release**.
3. Use a tag such as `v2-beta-stable-rc2` and a title such as
   `Studio One Project Tools v2 Beta Stable RC2`.
4. Paste the relevant section of `RELEASE_NOTES.md`.
5. Attach the Windows release ZIP as an asset.

Do not commit `es.exe` to a public repository, and only include it in a release ZIP if its
redistribution terms allow it.
