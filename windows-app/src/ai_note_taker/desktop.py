"""Native Windows meeting recorder with a compact floating control panel."""

import argparse
import math
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from PySide6.QtCore import QEvent, QPoint, QRectF, QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QSystemTrayIcon, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ai_note_taker.ai import DEFAULT_MODEL
from ai_note_taker.audio import devices
from ai_note_taker.desktop_controller import DesktopController

ACTIVE = {"starting", "recording", "paused", "processing"}
_fonts_loaded = False


def load_fonts() -> None:
    """Load installed Windows fonts explicitly, including in Qt offscreen renders."""
    global _fonts_loaded
    if _fonts_loaded:
        return
    directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "seguisym.ttf"):
        path = directory / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    _fonts_loaded = True


STYLE = """
QWidget { color: #292724; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, #canvas { background: #faf9f6; }
#sidebar { background: #eeede8; border-right: 1px solid #deddd7; }
#settings { background: #f3f2ee; border: 1px solid #e4e2db; border-radius: 14px; }
#card { background: white; border: 1px solid #e4e2db; border-radius: 14px; }
#brand { font-size: 19px; font-weight: 700; letter-spacing: -0.5px; }
#heading { font-size: 29px; font-weight: 650; letter-spacing: -0.7px; }
#eyebrow { color: #817d75; font-size: 11px; font-weight: 650; }
#muted { color: #817d75; }
#status { color: #925137; background: #f5e5db; border-radius: 11px; padding: 5px 12px; font-weight: 600; }
#time { font-size: 43px; font-weight: 600; letter-spacing: -1px; }
QPushButton { background: #fff; border: 1px solid #dcd9d1; border-radius: 8px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #f1eee7; border-color: #c0baad; }
QPushButton:pressed { background: #e7e1d6; }
QPushButton:disabled { color: #aaa69f; background: #f2f0eb; border-color: #e5e2db; }
QPushButton#primary { background: #be5b3d; border-color: #be5b3d; color: white; }
QPushButton#primary:hover { background: #a84e34; }
QPushButton#primary:disabled { background: #dbb5a7; border-color: #dbb5a7; }
QLineEdit, QComboBox { background: white; border: 1px solid #dbd8d0; border-radius: 7px; padding: 8px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #b66b4f; }
QComboBox::drop-down { border: none; width: 22px; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QTextBrowser { background: white; border: none; padding: 12px; font-size: 14px; selection-background-color: #edcbb7; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: transparent; color: #8a847a; padding: 13px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #a94e32; border-bottom: 2px solid #be5b3d; }
QListWidget { border: none; background: transparent; outline: none; }
QListWidget::item { padding: 12px 8px; border-radius: 7px; margin-bottom: 3px; }
QListWidget::item:selected { background: #dfddd5; color: #292724; }
QListWidget::item:hover { background: #e6e3db; }
QMenu { background: #faf9f6; padding: 6px; border: 1px solid #dad6cd; }
QMenu::item { padding: 8px 20px; border-radius: 4px; }
QMenu::item:selected { background: #ece6dc; }
QToolTip { background: #292724; color: #fff; border: none; padding: 5px; }
"""


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, callback, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#be5b3d"))
    painter.drawRoundedRect(0, 0, 64, 64, 16, 16)
    painter.setBrush(QColor("#fff9ef"))
    for x, height in ((14, 14), (23, 30), (32, 42), (41, 24)):
        painter.drawRoundedRect(x, (64-height)//2, 5, height, 2, 2)
    painter.end()
    return QIcon(pixmap)


class Waveform(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.level = 0.0
        self.phase = 0
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        count = max(1, self.width() // 9)
        for n in range(count):
            modulation = .25 + .75 * abs(math.sin(n * .82 + self.phase * .3))
            height = 4 + min(1, self.level * 3) * modulation * 48
            painter.setBrush(QColor("#be5b3d" if self.level > .001 else "#e4dfd5"))
            painter.drawRoundedRect(n * 9, int((64-height)/2), 4, int(height), 2, 2)
        painter.end()


class RecorderBubble(QPushButton):
    """Circular recorder handle; dragging must never activate its click action."""

    def __init__(self, mini: "MiniRecorder") -> None:
        super().__init__(mini)
        self.mini = mini
        self.setFixedSize(76, 76)
        self.setAccessibleName("Open recorder controls")
        self.setToolTip("Click for recording controls. Drag to move. Right-click for more.")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(mini.show_controls)
        self.press_position: QPoint | None = None
        self.window_position = QPoint()
        self.dragged = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#d5cfc4"), 1))
        painter.setBrush(QColor("#292724" if not self.isDown() else "#474039"))
        painter.drawEllipse(QRectF(3, 3, 69, 69))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#fff8ec"))
        for x, height in ((22, 14), (30, 28), (38, 36), (46, 21)):
            painter.drawRoundedRect(x, (76-height)//2, 4, height, 2, 2)
        state = self.mini.owner.status
        color = "#e77f58" if state == "recording" else "#e5be70" if state == "paused" else "#a8a294"
        painter.setPen(QPen(QColor("#f8f5ef"), 2))
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(54, 54, 14, 14))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.globalPosition().toPoint()
            self.window_position = self.mini.pos()
            self.dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.press_position
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self.dragged = True
            if self.dragged:
                self.mini.move_clamped(self.window_position + delta)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.press_position = None
        if self.dragged:
            self.setDown(False)
            self.mini.save_position()
            self.dragged = False
            return
        super().mouseReleaseEvent(event)


class MiniRecorder(QWidget):
    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.owner = owner
        self.setWindowTitle("AI Note Taker · Mini recorder")
        self.setObjectName("mini")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE + "#mini { background: transparent; } "
                          "#miniPanel { background: #f8f5ef; border: 1px solid #d7d0c3; border-radius: 18px; }")
        self.expanded = False
        self.drag_offset: QPoint | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.bubble = RecorderBubble(self)
        outer.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignCenter)
        self.bubble_minimize = button("−", owner.minimize_popup)
        self.bubble_minimize.setParent(self)
        self.bubble_minimize.setAccessibleName("Minimize recorder bubble")
        self.bubble_minimize.setToolTip("Minimize popup. Restore the app from the taskbar.")
        self.bubble_minimize.setFixedSize(22, 22)
        self.bubble_minimize.move(57, 0)
        self.bubble_minimize.setStyleSheet("padding: 0; background: #f8f5ef; border: 1px solid #d7d0c3; border-radius: 11px; font-size: 17px;")
        self.panel = QFrame()
        self.panel.setObjectName("miniPanel")
        outer.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(13, 11, 13, 12)
        layout.setSpacing(7)
        top = QHBoxLayout()
        top.addWidget(label("NOTE TAKER", "eyebrow"))
        top.addStretch()
        self.minimize_button = button("−", owner.minimize_popup)
        self.minimize_button.setAccessibleName("Minimize popup")
        self.minimize_button.setToolTip("Hide popup; keep the app in the taskbar.")
        self.minimize_button.setFixedSize(27, 25)
        self.minimize_button.setStyleSheet("padding: 0; border: none; font-size: 22px;")
        top.addWidget(self.minimize_button)
        self.close_button = button("×", self.close)
        self.close_button.setAccessibleName("Close mini recorder")
        self.close_button.setToolTip("Close popup; recording continues. Restore from the taskbar or tray.")
        self.close_button.setFixedSize(27, 25)
        self.close_button.setStyleSheet("padding: 0; border: none; font-size: 22px;")
        top.addWidget(self.close_button)
        layout.addLayout(top)
        self.status = label("Ready", "eyebrow", True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        self.time = label("00:00", "brand")
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setStyleSheet("font-size: 27px; font-weight: 650; padding: 5px 0;")
        layout.addWidget(self.time)
        self.toggle = button("Record", owner.mini_toggle, True)
        self.stop = button("Stop", owner.stop)
        layout.addWidget(self.toggle)
        layout.addWidget(self.stop)
        self.expand = button("Open full app", owner.restore)
        self.expand.setAccessibleName("Expand recorder")
        layout.addWidget(self.expand)
        self.collapse_button = button("Collapse to circle", self.collapse)
        self.collapse_button.setStyleSheet("padding: 5px; border: none; color: #817d75; background: transparent;")
        layout.addWidget(self.collapse_button)
        self.quit_button = button("Quit app", owner.quit_app)
        self.quit_button.setToolTip("Stop recording, finish saving, and exit the app.")
        self.quit_button.setStyleSheet("padding: 5px; border: none; color: #817d75; background: transparent;")
        layout.addWidget(self.quit_button)
        self.collapse()

    def change_mode(self, expanded: bool) -> None:
        old_right = self.x() + self.width()
        visible = self.isVisible()
        self.expanded = expanded
        self.bubble.setVisible(not expanded)
        self.bubble_minimize.setVisible(not expanded)
        self.panel.setVisible(expanded)
        self.setFixedSize(216, 338) if expanded else self.setFixedSize(80, 80)
        self.bubble_minimize.raise_()
        if visible:
            self.move_clamped(QPoint(old_right - self.width(), self.y()))

    def show_controls(self) -> None:
        self.change_mode(True)

    def collapse(self) -> None:
        self.change_mode(False)

    def move_clamped(self, point: QPoint) -> None:
        screen = QApplication.screenAt(point) or self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(max(area.left(), min(point.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(point.y(), area.bottom() - self.height() + 1)))

    def save_position(self) -> None:
        # Remember the circle's anchor even when the larger panel was dragged.
        point = QPoint(self.x() + self.width() - 80, self.y())
        self.owner.preferences.setValue("mini_position", point)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        for title, callback in (("Recording controls", self.show_controls),
                                ("Collapse to circle", self.collapse),
                                ("Minimize popup", self.owner.minimize_popup),
                                ("Open full app", self.owner.restore),
                                ("Quit app", self.owner.quit_app)):
            menu.addAction(title, callback)
        menu.exec(event.globalPos())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint() - self.drag_offset
            self.move_clamped(point)

    def mouseReleaseEvent(self, event) -> None:
        self.drag_offset = None
        self.save_position()

    def closeEvent(self, event) -> None:
        event.accept()
        self.owner.popup_dismissed = True


class MainWindow(QMainWindow):
    def __init__(self, output: Path, env_file: Path | None = None) -> None:
        super().__init__()
        load_fonts()
        self.preferences = QSettings("AI Note Taker", "Desktop")
        self.controller = DesktopController(output)
        self.env_file = env_file or self.find_config()
        self.selected: Path | None = None
        self.view_new = False
        self.status = "idle"
        self.quitting = False
        self.popup_dismissed = False
        self.last_session_ids: list[str] = []
        self.setWindowTitle("AI Note Taker")
        self.setWindowIcon(icon())
        self.resize(1160, 730)
        self.setMinimumSize(960, 620)
        self.setStyleSheet(STYLE)
        container = QWidget()
        container.setObjectName("canvas")
        self.setCentralWidget(container)
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(17, 25, 17, 20)
        left.addWidget(label("◧  Note Taker", "brand"))
        left.addSpacing(20)
        left.addWidget(button("+  New meeting", self.new_meeting))
        left.addSpacing(25)
        left.addWidget(label("YOUR RECORDINGS", "eyebrow"))
        self.history = QListWidget()
        self.history.setAccessibleName("Saved recording sessions")
        self.history.itemClicked.connect(self.select_session)
        left.addWidget(self.history, 1)
        left.addWidget(button("Open recordings", self.open_recordings))
        left.addWidget(label("Audio and transcripts\nstay in your recordings folder.", "muted", True))
        outer.addWidget(sidebar)

        body = QVBoxLayout()
        body.setContentsMargins(30, 25, 26, 25)
        body.setSpacing(18)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(label("MEETING WORKSPACE", "eyebrow"))
        heading.addWidget(label("Catch every detail.", "heading"))
        header.addLayout(heading, 1)
        header.addWidget(button("Mini recorder  ↗", self.show_mini))
        body.addLayout(header)
        self.message = label("Record your meeting. Get a transcript and useful notes.", "muted", True)
        body.addWidget(self.message)

        columns = QHBoxLayout()
        columns.setSpacing(20)
        work = QVBoxLayout()
        recorder = QFrame()
        recorder.setObjectName("card")
        recording = QVBoxLayout(recorder)
        recording.setContentsMargins(22, 18, 22, 20)
        state_row = QHBoxLayout()
        self.badge = label("Ready to record", "status")
        state_row.addWidget(self.badge)
        state_row.addStretch()
        self.source_label = label("SYSTEM AUDIO", "eyebrow")
        state_row.addWidget(self.source_label)
        recording.addLayout(state_row)
        self.clock = label("00:00", "time")
        recording.addWidget(self.clock)
        self.waveform = Waveform()
        recording.addWidget(self.waveform)
        controls = QHBoxLayout()
        self.record_button = button("●  Record", self.record, True)
        self.record_button.setAccessibleName("Record meeting")
        self.pause_button = button("Pause", self.toggle_pause)
        self.pause_button.setAccessibleName("Pause or resume recording")
        self.stop_button = button("Stop", self.stop)
        controls.addWidget(self.record_button, 1)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        recording.addLayout(controls)
        work.addWidget(recorder)

        document = QFrame()
        document.setObjectName("card")
        document_layout = QVBoxLayout(document)
        document_layout.setContentsMargins(8, 0, 8, 8)
        self.tabs = QTabWidget()
        self.transcript = QTextBrowser()
        self.transcript.setAccessibleName("Meeting transcript")
        self.transcript.setPlaceholderText("Your transcript will appear here as audio chunks are processed.\n\n"
                                           "Original audio is saved throughout the recording.")
        self.notes = QTextBrowser()
        self.notes.setPlaceholderText("Summary, decisions, and action items appear after you stop recording.")
        self.tabs.addTab(self.transcript, "Transcript")
        self.tabs.addTab(self.notes, "Meeting notes")
        document_layout.addWidget(self.tabs)
        work.addWidget(document, 1)
        columns.addLayout(work, 1)

        panel = QFrame()
        panel.setObjectName("settings")
        panel.setFixedWidth(255)
        settings = QVBoxLayout(panel)
        settings.setContentsMargins(17, 20, 17, 18)
        settings.setSpacing(10)
        settings.addWidget(label("CAPTURE SETTINGS", "eyebrow"))
        settings.addWidget(label("System audio"))
        self.system = QComboBox()
        self.system.setAccessibleName("System audio device")
        self.system.addItem("Default speakers / headphones", None)
        settings.addWidget(self.system)
        settings.addWidget(label("Windows master volume can be zero or muted.", "muted", True))
        self.microphone = QCheckBox("Include my microphone")
        self.microphone.setChecked(self.preferences.value("microphone", False, type=bool))
        settings.addWidget(self.microphone)
        self.mic = QComboBox()
        self.mic.setAccessibleName("Microphone device")
        self.mic.addItem("Default microphone", None)
        self.mic.setEnabled(self.microphone.isChecked())
        self.microphone.toggled.connect(self.mic.setEnabled)
        settings.addWidget(self.mic)
        self.offline = QCheckBox("Save audio only")
        self.offline.setToolTip("No AI requests. You can transcribe this session later from the CLI.")
        settings.addWidget(self.offline)
        settings.addWidget(label("Spoken language"))
        self.language = QLineEdit("auto")
        self.language.setAccessibleName("Spoken language")
        settings.addWidget(self.language)
        settings.addSpacing(8)
        settings.addWidget(label("AI CONFIGURATION", "eyebrow"))
        self.config_label = label("", "muted", True)
        settings.addWidget(self.config_label)
        self.config_button = button("Choose .env file…", self.choose_config)
        settings.addWidget(self.config_button)
        settings.addWidget(button("Change recordings folder…", self.choose_output))
        settings.addStretch()
        settings.addWidget(label("The meeting must keep playing. Audio goes to Gemini when AI is enabled.", "muted", True))
        columns.addWidget(panel)
        body.addLayout(columns, 1)
        self.footer = label(str(output), "muted")
        self.footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.footer)
        outer.addLayout(body, 1)

        self.mini = MiniRecorder(self)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        for title, action in (("Open Note Taker", self.restore), ("Mini recorder", self.show_mini),
                              ("Pause / Resume", self.toggle_pause), ("Stop recording", self.stop),
                              ("Quit", self.quit_app)):
            item = QAction(title, self)
            item.triggered.connect(action)
            menu.addAction(item)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore()
                                   if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(350)
        self.update_config_label()
        self.refresh()
        # Device enumeration is short, but defer it until the window is painted.
        QTimer.singleShot(100, self.load_devices)

    def find_config(self) -> Path | None:
        saved = self.preferences.value("env_file", "")
        if saved and Path(saved).is_file():
            return Path(saved)
        start = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        for parent in (start, *list(start.parents)[:5]):
            if (parent / ".env").is_file():
                return parent / ".env"
        return None

    def update_config_label(self) -> None:
        values = dotenv_values(self.env_file) if self.env_file else {}
        provider = values.get("ALM_LLM_PROVIDER") or os.getenv("ALM_LLM_PROVIDER", "aistudio")
        self.config_label.setText(f"{provider}\n{self.env_file.name if self.env_file else 'Environment variables'}")
        self.config_label.setToolTip(str(self.env_file or "Uses environment variables"))

    def load_devices(self) -> None:
        try:
            for device in devices():
                combo = self.system if device["loopback"] else self.mic
                combo.addItem(f"{device['index']} · {device['name']}", device["index"])
        except Exception as exc:
            self.message.setText(f"Could not list audio devices ({type(exc).__name__}).")

    def choose_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose AI configuration", "", "Environment files (*.env);;All files (*)")
        if path:
            self.env_file = Path(path)
            self.preferences.setValue("env_file", path)
            self.update_config_label()

    def choose_output(self) -> None:
        if self.status in ACTIVE:
            self.message.setText("Finish the current recording before changing the folder.")
            return
        path = QFileDialog.getExistingDirectory(self, "Recordings folder", str(self.controller.output))
        if path:
            self.preferences.setValue("output", path)
            self.controller = DesktopController(Path(path))
            self.selected = None
            self.last_session_ids = []
            self.footer.setText(path)
            self.refresh()

    def record(self) -> None:
        values = {**os.environ, **({k: v for k, v in dotenv_values(self.env_file).items() if v is not None}
                                  if self.env_file else {})}
        mode = values.get("ALM_LLM_PROVIDER", values.get("AUTH_MODE", "aistudio"))
        if not self.offline.isChecked() and mode not in ("vertex_adc", "vertex", "vertex_iam") and not values.get("GEMINI_API_KEY"):
            self.restore()
            QMessageBox.information(self, "AI configuration needed",
                                    "Choose a .env file with GEMINI_API_KEY, or enable Save audio only.")
            return
        config = {"microphone": self.microphone.isChecked(), "system_device": self.system.currentData(),
                  "mic_device": self.mic.currentData() if self.microphone.isChecked() else None,
                  "record_only": self.offline.isChecked(), "chunk_seconds": 20, "duration": None,
                  "model": values.get("GEMINI_MODEL") or values.get("MODEL_ID", DEFAULT_MODEL),
                  "language": self.language.text().strip() or "auto"}
        try:
            self.selected = self.controller.start(config, self.env_file)
            self.view_new = False
            self.preferences.setValue("microphone", self.microphone.isChecked())
            self.refresh()
        except Exception as exc:
            self.message.setText(str(exc))

    def mini_toggle(self) -> None:
        if self.status in ("recording", "paused"):
            self.toggle_pause()
        elif self.status not in ACTIVE:
            self.record()

    def toggle_pause(self) -> None:
        if self.status == "recording":
            self.controller.pause()
        elif self.status == "paused":
            self.controller.resume()

    def stop(self) -> None:
        self.controller.stop()
        if self.status in ("recording", "paused", "starting"):
            self.message.setText("Stopping capture and finishing your transcript…")
            self.stop_button.setEnabled(False)
            self.mini.stop.setEnabled(False)

    def show_mini(self) -> None:
        self.popup_dismissed = False
        if not self.isMinimized():
            self.showMinimized()
        self.show_mini_if_minimized()

    def show_mini_if_minimized(self) -> None:
        if not self.isMinimized() or self.popup_dismissed or self.quitting:
            return
        if not self.mini.isVisible():
            self.mini.collapse()
            screen = self.screen() or QApplication.primaryScreen()
            rect = screen.availableGeometry()
            saved = self.preferences.value("mini_position")
            position = saved if isinstance(saved, QPoint) else QPoint(
                rect.right() - self.mini.width() - 22, rect.top() + rect.height() // 3)
            self.mini.move(max(rect.left(), min(position.x(), rect.right() - self.mini.width() + 1)),
                           max(rect.top(), min(position.y(), rect.bottom() - self.mini.height() + 1)))
        self.mini.show()

    def minimize_popup(self) -> None:
        self.popup_dismissed = True
        self.mini.hide()

    def restore(self) -> None:
        self.mini.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "mini"):
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            if self.isMinimized() and not was_minimized:
                self.popup_dismissed = False
                self.show_mini_if_minimized()
            elif not self.isMinimized():
                self.mini.hide()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self.quitting:
            event.accept()
        else:
            event.ignore()
            self.show_mini()

    def quit_app(self) -> None:
        self.quitting = True
        self.controller.stop()
        # The hidden worker finishes saving/AI requests even after the UI exits.
        self.tray.hide()
        self.mini.hide()
        QApplication.quit()

    def open_recordings(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.selected or self.controller.output)))

    def new_meeting(self) -> None:
        if self.status in ACTIVE:
            self.message.setText("Stop the current recording before starting a new meeting.")
            return
        self.selected = None
        self.view_new = True
        self.history.clearSelection()
        self.transcript.clear()
        self.notes.clear()
        self.message.setText("Ready for your next meeting. Press Record to begin.")

    def select_session(self, item: QListWidgetItem) -> None:
        self.selected = Path(item.data(Qt.ItemDataRole.UserRole))
        self.view_new = False
        self.update_documents()

    def update_documents(self) -> None:
        for target, text in ((self.transcript, "" if self.view_new else self.controller.transcript(self.selected)),
                             (self.notes, "" if self.view_new else self.controller.notes(self.selected))):
            if target.property("cached_text") != text:
                scroll = target.verticalScrollBar()
                at_bottom = scroll.value() >= scroll.maximum() - 10
                old = scroll.value()
                if target is self.notes:
                    target.setMarkdown(text)
                else:
                    target.setPlainText(text)
                target.setProperty("cached_text", text)
                scroll.setValue(scroll.maximum() if at_bottom else old)

    def refresh(self) -> None:
        try:
            state = self.controller.snapshot()
        except (OSError, ValueError):
            return
        self.status = state["status"]
        busy = self.status in ACTIVE
        title = {"idle": "Ready to record", "starting": "Starting…", "recording": "Recording",
                 "paused": "Paused", "processing": "Finishing notes…", "stopped": "Saved",
                 "failed": "Recording error", "interrupted": "Recording interrupted"}.get(self.status, self.status)
        self.badge.setText(title)
        seconds = int(state.get("active_seconds", 0))
        clock = f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}" if seconds >= 3600 else f"{seconds//60:02}:{seconds%60:02}"
        self.clock.setText(clock)
        self.record_button.setEnabled(not busy)
        self.pause_button.setEnabled(self.status in ("recording", "paused"))
        self.pause_button.setText("Resume" if self.status == "paused" else "Pause")
        self.stop_button.setEnabled(self.status in ("recording", "paused", "starting"))
        for widget in (self.system, self.microphone, self.offline, self.config_button, self.language):
            widget.setEnabled(not busy)
        self.mic.setEnabled(not busy and self.microphone.isChecked())
        self.source_label.setText("SYSTEM + MIC" if state.get("config", {}).get("microphone") else "SYSTEM AUDIO")
        messages = {"recording": "Listening to system audio. New transcript chunks appear about every 20 seconds.",
                    "paused": "Recording paused. Audio during this pause is discarded.",
                    "processing": "Audio saved. Gemini is finishing your transcript and meeting notes…",
                    "starting": "Opening the audio devices…", "stopped": "Recording saved.",
                    "interrupted": "Recording was interrupted. Saved audio can be recovered with the CLI.",
                    "failed": state.get("error", f"Recorder failed ({state.get('error_type', 'unknown')}). See recorder.log.")}
        if self.status in messages:
            message = messages[self.status]
            processing = state.get("processing", {})
            if self.status == "stopped" and processing.get("status") in ("pending", "notes_failed", "failed"):
                message += " Some AI work is pending; use the CLI transcribe command to retry."
            elif self.status == "stopped" and processing.get("status") == "complete":
                message += " Transcript and meeting notes are ready."
            elif state.get("config", {}).get("record_only"):
                message = "Recording audio locally. AI is disabled." if self.status == "recording" else message
            self.message.setText(message)
        self.waveform.level = max(state.get("levels", {}).values(), default=0) if self.status == "recording" else 0
        self.waveform.phase += 1
        self.waveform.update()
        self.mini.status.setText(f"{'●' if self.status == 'recording' else '◦'}  {title}")
        self.mini.time.setText(clock)
        self.mini.toggle.setText("Resume" if self.status == "paused" else "Pause" if self.status == "recording" else "Record")
        self.mini.toggle.setEnabled(self.status in ("recording", "paused") or not busy)
        self.mini.stop.setEnabled(self.stop_button.isEnabled())
        self.mini.bubble.setToolTip(f"{title} · {clock}\nClick for controls. Drag to move.")
        self.mini.bubble.update()
        self.tray.setToolTip(f"AI Note Taker · {title} · {clock}")
        sessions = self.controller.sessions()
        ids = [str(path) for path in sessions]
        if ids != self.last_session_ids:
            self.history.clear()
            for path in sessions:
                item = QListWidgetItem(f"Meeting · {path.parent.name[9:11]}:{path.parent.name[11:13]}\n{path.parent.name[:8]}")
                item.setData(Qt.ItemDataRole.UserRole, str(path.parent))
                item.setToolTip(path.parent.name)
                self.history.addItem(item)
            self.last_session_ids = ids
        self.update_documents()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Note Taker desktop")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if sys.platform == "win32":
        import ctypes

        # Keep the taskbar entry identifiable as this app when launched by Python.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AINoteTaker.Desktop")
    application = QApplication(sys.argv[:1])
    application.setApplicationName("AI Note Taker")
    application.setOrganizationName("AI Note Taker")
    application.setQuitOnLastWindowClosed(False)
    application.setFont(QFont("Segoe UI", 10))
    preferences = QSettings("AI Note Taker", "Desktop")
    default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "AI Note Taker" / "Recordings"
    output = args.output or Path(preferences.value("output", str(default)))
    window = MainWindow(output, args.env_file)
    window.show()
    raise SystemExit(application.exec())
