"""Render the app icon to packaging/app.ico (PNG-compressed, 16-256 px). Run: uv run python packaging/make_icon.py"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication

from ai_note_taker.desktop import icon

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    app = QGuiApplication([])
    source = icon()
    images = []
    for size in SIZES:
        pixmap = source.pixmap(size, size).scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        images.append((size, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    target = Path(__file__).with_name("app.ico")
    target.write_bytes(header + entries + blobs)
    print(f"Wrote {target} ({target.stat().st_size} bytes)")
    del app


if __name__ == "__main__":
    main()
