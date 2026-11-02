@echo off
REM One-time setup for Windows. Run from the repo root:  scripts\setup.bat
setlocal
cd /d "%~dp0..\backend"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)

echo 1/5  Creating the Python virtual environment (backend\.venv)
%PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul

echo 2/5  Installing Python packages
pip install -r requirements.txt || goto :fail

echo 3/5  Preparing backend\.env
if not exist .env (
  copy .env.example .env >nul
  echo      Created backend\.env. Add your ANTHROPIC_API_KEY there to turn on Claude.
) else (
  echo      backend\.env already exists, leaving it alone.
)

echo 4/5  Creating the database and a fresh synthetic shift
python manage.py migrate --noinput || goto :fail
python manage.py seed_demo || goto :fail

echo 5/5  Building the React app
cd /d "%~dp0..\frontend"
where npm >nul 2>nul
if %errorlevel%==0 (
  call npm install || goto :fail
  call npm run build || goto :fail
) else (
  if exist dist\index.html (
    echo      npm not found, using the prebuilt frontend\dist that came in the zip.
  ) else (
    echo      npm not found and no prebuilt app. Install Node.js 20 or newer, then run: cd frontend ^&^& npm install ^&^& npm run build
  )
)

echo.
echo Done. Start the app with:  scripts\start.bat
echo Then open http://localhost:8000
exit /b 0

:fail
echo Setup stopped because a step failed. Read the error above.
exit /b 1
