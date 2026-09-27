@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found. Install uv or use the PyCharm terminal.
    pause
    exit /b 1
)
uv run --locked streamlit run app.py
if errorlevel 1 pause
