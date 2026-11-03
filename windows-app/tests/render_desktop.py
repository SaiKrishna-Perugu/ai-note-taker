"""Render preview artifacts with Qt's offscreen backend; no desktop interaction."""

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from ai_note_taker.desktop import MainWindow

app = QApplication([])
window = MainWindow(Path("recordings/preview"))
window.timer.stop()
window.show()
app.processEvents()
destination = Path("artifacts")
destination.mkdir(exist_ok=True)
window.grab().save(str(destination / "desktop.png"))
window.show_mini()
app.processEvents()
window.mini.grab().save(str(destination / "mini-recorder.png"))
window.mini.show_controls()
app.processEvents()
window.mini.grab().save(str(destination / "mini-controls.png"))
window.quitting = True
window.tray.hide()
window.close()
print(destination.resolve())
