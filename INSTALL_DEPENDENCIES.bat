@echo off
cd /d "%~dp0"
chcp 65001 >nul
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m pip install --user --upgrade -r requirements.txt
) else (
  python -m pip install --user --upgrade -r requirements.txt
)
if errorlevel 1 echo WARNING: package installation failed; check Internet or Python pip.
pause
