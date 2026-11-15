@echo off
rem Trader Desk Pro: first run sets up Python + the UI build, then serves on http://127.0.0.1:8766
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :err
  .venv\Scripts\python -m pip install -q -e "backend[dev]" || goto :err
)
if not exist frontend\dist\index.html (
  pushd frontend
  call npm install --no-audit --no-fund || goto :err
  call npm run build || goto :err
  popd
)
echo Trader Desk Pro on http://127.0.0.1:8766
.venv\Scripts\python -m traderdesk.app.main
goto :eof
:err
echo Setup failed. See the messages above.
exit /b 1
