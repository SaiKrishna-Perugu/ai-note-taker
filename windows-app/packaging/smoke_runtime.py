"""Run with the *bundled* interpreter to prove the staged app is complete. Exits non-zero on failure."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import google.genai  # noqa: F401
import psutil  # noqa: F401
import pyaudiowpatch  # noqa: F401
from PySide6.QtWidgets import QApplication

from ai_note_taker import desktop

assert "site-packages" in Path(desktop.__file__).parts, f"not running the staged copy: {desktop.__file__}"
application = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    window = desktop.MainWindow(Path(folder))
    window.timer.stop()
    window.popup.update_state("recording", "00:05")
    window.quitting = True
    window.popup.dispose()
    window.tray.hide()
print(f"runtime ok: Python {sys.version.split()[0]}, app at {Path(desktop.__file__).parent}")
