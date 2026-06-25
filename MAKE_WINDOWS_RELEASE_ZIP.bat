@echo off
setlocal
cd /d "%~dp0"

if not exist "dist\StudioOneProjectTools\StudioOneProjectTools.exe" (
    echo ERROR: Build first using BUILD_WINDOWS_EXE.bat
    pause
    exit /b 1
)

set RELEASE=StudioOneProjectTools_v2_Beta_Stable_RC1_Windows
if exist "%RELEASE%" rmdir /s /q "%RELEASE%"
mkdir "%RELEASE%"

xcopy "dist\StudioOneProjectTools" "%RELEASE%\StudioOneProjectTools" /E /I /Y
copy "MANUAL_RU_EN.docx" "%RELEASE%\" >nul
copy "MANUAL_RU_EN.pdf" "%RELEASE%\" >nul
copy "PROMO_RU_EN.txt" "%RELEASE%\" >nul
copy "WINDOWS_BUILD_README_RU_EN.md" "%RELEASE%\" >nul
copy "VERSION.txt" "%RELEASE%\StudioOneProjectTools\" >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%RELEASE%' -DestinationPath '%RELEASE%.zip' -Force"

echo.
echo Release package created:
echo %CD%\%RELEASE%.zip
echo.
pause
