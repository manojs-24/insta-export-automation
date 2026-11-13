@echo off
cd /d "%~dp0"
uv run web-automation --keep-open %*
pause
