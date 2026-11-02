@echo off
REM Start Sift on http://localhost:8000. Run from the repo root:  scripts\start.bat
REM Add --fresh to generate a new synthetic shift first.
setlocal
cd /d "%~dp0..\backend"
if not exist .venv\Scripts\activate.bat (
  echo Run scripts\setup.bat first.
  exit /b 1
)
call .venv\Scripts\activate.bat
if "%~1"=="--fresh" python manage.py seed_demo
echo Sift is starting on http://localhost:8000  (Ctrl+C to stop)
python manage.py runserver 8000
