@echo off
cd /d "%~dp0.."
if not exist ".local-data" mkdir ".local-data"
echo [1/3] Running tests...
".venv\Scripts\python.exe" -m unittest discover -s tests > ".local-data\b0101-tests.txt" 2>&1
if errorlevel 1 (echo TESTS FAILED> ".local-data\b0101-status.txt" & echo Tests failed, see .local-data\b0101-tests.txt & pause & exit /b 1)
echo [2/3] Building EXE, this takes 1-2 minutes...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build.ps1" > ".local-data\b0102-build.txt" 2>&1
if errorlevel 1 (echo BUILD FAILED> ".local-data\b0102-status.txt" & echo Build failed, see .local-data\b0102-build.txt & pause & exit /b 1)
echo [3/3] Copying to Desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath(Desktop); Copy-Item -LiteralPath dist\WeWriter-v0.10.2-Windows.zip -Destination $d -Force; Copy-Item -LiteralPath dist\v0.10.2\WeWriter.exe -Destination (Join-Path $d WeWriter-v0.10.2.exe) -Force; Set-Content -LiteralPath .local-data\b0102-status.txt -Value ('DONE ' + $d)"
if errorlevel 1 (echo COPY FAILED> ".local-data\b0102-status.txt" & pause & exit /b 1)
echo All done. Files are on your Desktop.
timeout /t 8
