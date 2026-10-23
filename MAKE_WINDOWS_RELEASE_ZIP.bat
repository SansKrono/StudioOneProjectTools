@echo off
setlocal
cd /d "%~dp0"

if not exist "dist\StudioOneProjectTools\StudioOneProjectTools.exe" (
    echo ERROR: Build first using BUILD_WINDOWS_EXE.bat
    pause
    exit /b 1
)

set RELEASE=StudioOneProjectTools_v2_Beta_Stable_RC2_Windows
if exist "%RELEASE%" rmdir /s /q "%RELEASE%"
if exist "%RELEASE%.zip" del "%RELEASE%.zip"
mkdir "%RELEASE%"

xcopy "dist\StudioOneProjectTools" "%RELEASE%\StudioOneProjectTools" /E /I /Y >nul
copy "docs\MANUAL.md" "%RELEASE%\" >nul
copy "README.md" "%RELEASE%\" >nul
copy "LICENSE" "%RELEASE%\" >nul
copy "WINDOWS_BUILD.md" "%RELEASE%\" >nul
copy "VERSION.txt" "%RELEASE%\StudioOneProjectTools\" >nul

rem tar.exe ships with Windows 10 1803+ and can write .zip archives.
tar -a -c -f "%RELEASE%.zip" "%RELEASE%"
if errorlevel 1 (
    echo ERROR: Could not create the ZIP archive.
    pause
    exit /b 1
)

echo.
echo Release package created:
echo %CD%\%RELEASE%.zip
echo.
pause
