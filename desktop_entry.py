"""
Entry point for the packaged Windows .exe -- always launches the desktop
GUI directly, with no command-line parsing, so double-clicking the built
executable just opens the app. (The regular `main.py --mode gui|web` is
still how the app runs from source; this only exists for PyInstaller.)
"""

from downloadify.gui.main_window import run_gui

if __name__ == "__main__":
    run_gui()
