# Builds Downloadify.exe (a standalone Windows executable of the desktop
# GUI) into dist\Downloadify.exe using PyInstaller.
#
# This is only needed to produce the downloadable .exe for the website --
# running Downloadify from source (`python main.py`) never requires
# PyInstaller. Re-run this after any change to the GUI to refresh the build.
#
# Usage (from the project root, in PowerShell):
#   .\build_exe.ps1

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv_build")) {
    python -m venv .venv_build
}

.\.venv_build\Scripts\python.exe -m pip install -q -r requirements.txt
.\.venv_build\Scripts\python.exe -m pip install -q pyinstaller

.\.venv_build\Scripts\python.exe -m PyInstaller `
    --name Downloadify `
    --onefile `
    --windowed `
    --icon "website\assets\downloadify.ico" `
    --add-data "downloadify\gui\style.qss;downloadify\gui" `
    --collect-all yt_dlp `
    --collect-all PyQt6 `
    --noconfirm `
    desktop_entry.py

Write-Host "`nBuilt: dist\Downloadify.exe" -ForegroundColor Green
