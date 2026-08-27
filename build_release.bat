@echo off
setlocal
cd /d "%~dp0"

echo Building C2K FPS Perception Test...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\build.ps1"
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%BUILD_EXIT%"=="0" (
    echo.
    echo Build failed. Review the error above.
    exit /b %BUILD_EXIT%
)

echo.
echo Build finished. See the dist folder.
exit /b 0
