"""Render preview artifacts with Qt's offscreen backend; no desktop interaction.

Usage: python tests/render_desktop.py [OUTPUT_DIR]

Writes desktop.png plus popup-idle.png, popup-recording.png and popup-paused.png
into OUTPUT_DIR (default: ./render-output, created if missing).
"""

import argparse
import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from ai_note_taker.desktop import MainWindow

POPUP_STATES = (
    ("idle", "00:00"),
    ("recording", "12:34"),
    ("paused", "12:34"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render desktop UI previews to PNG files.")
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=Path("./render-output"),
        help="directory to write PNGs into (default: ./render-output)",
    )
    return parser.parse_args()


def main() -> None:
    destination = parse_args().output_dir
    destination.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    window = MainWindow(Path("recordings/preview"))
    window.timer.stop()
    window.show()
    app.processEvents()
    window.grab().save(str(destination / "desktop.png"))

    popup = window.popup
    popup.show()
    for status, clock in POPUP_STATES:
        popup.update_state(status, clock)
        app.processEvents()
        popup.grab().save(str(destination / f"popup-{status}.png"))

    window.quitting = True
    window.tray.hide()
    window.close()
    popup.dispose()
    print(destination.resolve())


if __name__ == "__main__":
    main()
