@echo off
rem Builds dist\GradeWatcher.exe. Run from the folder with app.py, grade_watch.py, ui.html and icon.ico.
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python not found - installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements || (
    echo Python install failed. Install it from python.org, then run this again.
    pause & exit /b 1
  )
  rem shortcut: a fresh install isn't on PATH in this window yet, so ask for a re-run instead of guessing paths
  echo.
  echo Python installed. Close this window and run build.bat again.
  pause & exit /b 0
)

py -m pip install --upgrade pyinstaller playwright keyring pywebview || (pause & exit /b 1)
py -m PyInstaller --noconfirm --onefile --noconsole --name GradeWatcher ^
  --icon icon.ico ^
  --add-data "ui.html;." ^
  --collect-all playwright ^
  --collect-all webview ^
  --copy-metadata keyring --hidden-import keyring.backends.Windows ^
  app.py || (pause & exit /b 1)

echo.
echo Done: %cd%\dist\GradeWatcher.exe  - this single file is all a parent needs.
pause
