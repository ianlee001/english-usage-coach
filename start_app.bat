@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Install uv or use the PyCharm terminal.
    pause
    exit /b 1
)
echo Open http://127.0.0.1:8000 after the server starts.
uv run --locked python -m backend.server
if errorlevel 1 pause
