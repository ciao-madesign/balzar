"""Entry point for the desktop app, used directly and by PyInstaller.

    python3 balzar-app.py

Build a standalone executable (Windows .exe / macOS .app / Linux binary):

    pip install pyinstaller pillow
    pyinstaller --onefile --windowed --name balzar balzar-app.py

The result in dist/ runs fully offline with no Python installation.
"""

from balzar.gui import main

if __name__ == "__main__":
    main()
