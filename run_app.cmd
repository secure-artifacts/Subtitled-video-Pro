@echo off
setlocal

cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
    ) else (
        set "PYTHON_EXE=python"
    )
)

echo Starting Subtitle Composer...
echo Directory: %CD%
echo Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" main.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo App exited with error code %EXIT_CODE%.
) else (
    echo App closed normally.
)
echo Press any key to close this window.
pause >nul
exit /b %EXIT_CODE%
