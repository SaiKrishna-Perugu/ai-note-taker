"""Native Windows meeting recorder with a compact floating control panel."""

import argparse
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import dotenv_values
from PySide6.QtCore import QEvent, QObject, QSettings, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase, QGuiApplication, QIcon,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSystemTrayIcon, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ai_note_taker.ai import DEFAULT_MODEL
from ai_note_taker.desktop_controller import DesktopController, list_devices
from ai_note_taker.popup import CapsuleRecorder
from ai_note_taker.theme import (PREFERENCE_LABELS, PREFERENCES, apply_color_scheme, normalize,
                                 palette, resolve, style_sheet)

ACTIVE = {"starting", "recording", "paused", "processing"}
_fonts_loaded = False
ENV_TEMPLATE = """# AI Note Taker settings. Keep this file private; never share your key.
# Create a key at https://aistudio.google.com/apikey and paste it below.
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
ALM_LLM_PROVIDER=aistudio
"""


def user_config_path() -> Path:
    """Per-user settings file used by the installed app (survives upgrades)."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "AI Note Taker" / ".env"


def save_api_key(key: str) -> Path:
    """Store the Gemini key in the per-user settings file, keeping its other settings."""
    key = key.strip()
    if not key or any(character.isspace() for character in key):
        raise ValueError("That does not look like an API key (it contains spaces or line breaks).")
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ENV_TEMPLATE.splitlines()
    saved, replaced = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == "GEMINI_API_KEY":
            saved.append(f"GEMINI_API_KEY={key}")
            replaced = True
        else:
            saved.append(line)
    if not replaced:
        saved.append(f"GEMINI_API_KEY={key}")
    path.write_text("\n".join(saved) + "\n", encoding="utf-8")
    return path


def set_tone(widget: QWidget, tone: str) -> None:
    """Switch a styled label between look variants (ok / warn / off) defined in the theme stylesheet."""
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


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


class DeviceLoader(QObject):
    """Lists audio devices off the UI thread (and in a child process) so a stuck Windows Audio
    service can never freeze the window."""

    loaded = Signal(list)
    failed = Signal(str)

    def start(self, timeout: float = 10.0) -> None:
        def work() -> None:
            try:
                self.loaded.emit(list_devices(timeout))
            except TimeoutError:
                self.failed.emit("Windows Audio is not answering. Default devices will be used.")
            except Exception as exc:
                self.failed.emit(f"Could not list audio devices ({type(exc).__name__}).")

        threading.Thread(target=work, daemon=True, name="device-loader").start()


class Waveform(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.level = 0.0
        self.phase = 0
        self.set_theme("light")
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_theme(self, theme: str) -> None:
        colors = palette(theme)
        self.idle_color, self.active_color = QColor(colors["wave_idle"]), QColor(colors["wave_active"])
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        count = max(1, self.width() // 9)
        for n in range(count):
            modulation = .25 + .75 * abs(math.sin(n * .82 + self.phase * .3))
            height = 4 + min(1, self.level * 3) * modulation * 48
            painter.setBrush(self.active_color if self.level > .001 else self.idle_color)
            painter.drawRoundedRect(n * 9, int((64-height)/2), 4, int(height), 2, 2)
        painter.end()


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
        self.theme_preference = normalize(self.preferences.value("theme", "system"))
        apply_color_scheme(self.theme_preference)
        self.theme_name = resolve(self.theme_preference)
        self.setStyleSheet(style_sheet(self.theme_name))
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
        header.addWidget(button("Floating recorder  ↗", lambda: self.show_popup(force=True)))
        body.addLayout(header)
        self.message = label("Record your meeting. Get a transcript and useful notes.", "muted", True)
        body.addWidget(self.message)
        # Amber call-to-action shown when a transcript cannot appear without the user doing something.
        self.notice = QFrame()
        self.notice.setObjectName("notice")
        notice_row = QHBoxLayout(self.notice)
        notice_row.setContentsMargins(16, 10, 10, 10)
        self.notice_text = label("", "noticeText", True)
        notice_row.addWidget(self.notice_text, 1)
        self.notice_button = button("", self.notice_action, True)
        notice_row.addWidget(self.notice_button)
        self.notice_kind = ""
        self.notice.hide()
        body.addWidget(self.notice)
        self.transcribe_started = False

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
        self.ai_chip = label("", "chip")
        self.ai_chip.setAccessibleName("AI transcription status")
        state_row.addWidget(self.ai_chip)
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
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        # Scrollable so settings are never squashed or clipped on short windows.
        scroller = QScrollArea()
        scroller.setObjectName("settingsScroll")
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_layout.addWidget(scroller)
        inner = QWidget()
        inner.setObjectName("settingsInner")
        scroller.setWidget(inner)
        settings = QVBoxLayout(inner)
        settings.setContentsMargins(17, 20, 17, 18)
        settings.setSpacing(10)
        settings.addWidget(label("APPEARANCE", "eyebrow"))
        self.theme_box = QComboBox()
        self.theme_box.setAccessibleName("Theme")
        self.theme_box.setToolTip("Match Windows follows your Windows light/dark setting, even while the app is open.")
        for preference in PREFERENCES:
            self.theme_box.addItem(PREFERENCE_LABELS[preference], preference)
        self.theme_box.setCurrentIndex(PREFERENCES.index(self.theme_preference))
        self.theme_box.currentIndexChanged.connect(
            lambda _index: self.set_theme_preference(self.theme_box.currentData()))
        settings.addWidget(self.theme_box)
        settings.addSpacing(8)
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
        self.offline.setToolTip("No AI requests while recording. You can transcribe the session later "
                                "with the Transcribe now button.")
        self.offline.toggled.connect(lambda _checked: self.update_chip())
        settings.addWidget(self.offline)
        settings.addWidget(label("Spoken language"))
        self.language = QLineEdit("auto")
        self.language.setAccessibleName("Spoken language")
        settings.addWidget(self.language)
        settings.addSpacing(8)
        settings.addWidget(label("AI CONFIGURATION", "eyebrow"))
        self.config_label = label("", "muted", True)
        settings.addWidget(self.config_label)
        self.key_button = button("Set Gemini API key…", self.set_api_key)
        self.key_button.setAccessibleName("Set Gemini API key")
        settings.addWidget(self.key_button)
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

        self.popup = CapsuleRecorder(self.record, self.toggle_pause, self.stop, self.dismiss_popup, self.preferences)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray_menu = menu = QMenu()
        for title, action in (("Open Note Taker", self.restore), ("Show floating recorder", lambda: self.show_popup(force=True)),
                              ("Pause / Resume", self.toggle_pause), ("Stop recording", self.stop),
                              ("Quit", self.request_quit)):
            item = QAction(title, self)
            item.triggered.connect(action)
            menu.addAction(item)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore()
                                   if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self.on_color_scheme_changed)   # Windows theme flipped
        self.apply_theme()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(350)
        self.update_config_label()
        self.refresh()
        # Device enumeration is short, but defer it until the window is painted.
        self.device_loader = DeviceLoader(self)
        self.device_loader.loaded.connect(self.apply_devices)
        self.device_loader.failed.connect(self.devices_failed)
        QTimer.singleShot(100, self.load_devices)

    def find_config(self) -> Path | None:
        saved = self.preferences.value("env_file", "")
        if saved and Path(saved).is_file():
            return Path(saved)
        if user_config_path().is_file():
            return user_config_path()
        if "site-packages" in Path(__file__).resolve().parts:
            # Installed copy: only the explicit/per-user config applies, never a stray .env
            # that happens to sit in a parent folder such as the user profile.
            return None
        start = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        for parent in (start, *list(start.parents)[:5]):
            if (parent / ".env").is_file():
                return parent / ".env"
        return None

    def set_theme_preference(self, preference: str) -> None:
        self.theme_preference = normalize(preference)
        self.preferences.setValue("theme", self.theme_preference)
        apply_color_scheme(self.theme_preference)
        self.apply_theme()

    def on_color_scheme_changed(self, *_args) -> None:
        if not self.quitting:
            self.apply_theme()

    def apply_theme(self) -> None:
        """Restyle everything for the resolved light/dark theme."""
        self.theme_name = resolve(self.theme_preference)
        sheet = style_sheet(self.theme_name)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        self.tray_menu.setStyleSheet(sheet)
        self.waveform.set_theme(self.theme_name)
        self.popup.set_theme(self.theme_name)

    def ai_values(self) -> dict:
        # Blank lines (e.g. the template's empty GEMINI_API_KEY=) must not hide a real value.
        extra = {k: v for k, v in dotenv_values(self.env_file).items() if v} if self.env_file else {}
        return {**os.environ, **extra}

    def has_ai_credentials(self) -> bool:
        values = self.ai_values()
        mode = values.get("ALM_LLM_PROVIDER", values.get("AUTH_MODE", "aistudio"))
        return mode in ("vertex_adc", "vertex", "vertex_iam") or bool(values.get("GEMINI_API_KEY"))

    def ai_state(self) -> str:
        """off = audio only, key = AI wanted but no key, ready = transcripts will be produced."""
        if self.offline.isChecked():
            return "off"
        return "ready" if self.has_ai_credentials() else "key"

    def update_chip(self) -> None:
        state = self.ai_state()
        text, tone = {"ready": ("AI TRANSCRIPT ON", "ok"),
                      "off": ("AUDIO ONLY · NO TRANSCRIPT", "off"),
                      "key": ("AI KEY NEEDED", "warn")}[state]
        self.ai_chip.setText(text)
        set_tone(self.ai_chip, tone)
        self.ai_chip.setToolTip({"ready": "Audio is sent to Gemini; you get a transcript and notes.",
                                 "off": "Nothing is sent to AI. Untick “Save audio only” for transcripts, "
                                        "or use Transcribe now later.",
                                 "key": "Add a Gemini API key to get transcripts."}[state])

    def prompt_for_api_key(self) -> bool:
        key, accepted = QInputDialog.getText(
            self, "Gemini API key",
            "Paste your Gemini API key (create one at aistudio.google.com/apikey).\n"
            "It is stored only on this PC, in your user settings. Audio is sent to Google's Gemini API "
            "when AI is on.", QLineEdit.EchoMode.Password)
        if not accepted or not key.strip():
            return False
        try:
            path = save_api_key(key)
        except ValueError as exc:
            QMessageBox.warning(self, "Gemini API key", str(exc))
            return False
        self.env_file = path
        self.preferences.setValue("env_file", str(path))
        self.update_config_label()
        self.update_chip()
        return True

    def set_api_key(self) -> None:
        if self.prompt_for_api_key():
            self.message.setText("API key saved. Transcripts are on for your next recording.")
            self.refresh()

    def update_config_label(self) -> None:
        values = dotenv_values(self.env_file) if self.env_file else {}
        provider = values.get("ALM_LLM_PROVIDER") or os.getenv("ALM_LLM_PROVIDER", "aistudio")
        key = "API key set" if self.has_ai_credentials() else "API key missing"
        self.config_label.setText(f"{provider} · {key}\n{self.env_file.name if self.env_file else 'Environment variables'}")
        self.config_label.setToolTip(str(self.env_file or "Uses environment variables"))

    def effective_session(self) -> Path | None:
        return self.selected or self.controller.effective_session()

    def update_notice(self, active: bool) -> None:
        """Tell the user exactly what stands between them and a transcript, with one button to fix it."""
        kind, text, action = "", "", ""
        if self.controller.transcribing():
            kind, text = "busy", "Transcribing your recording… text appears below as each part finishes."
        elif not active:
            session = None if self.view_new else self.effective_session()
            info = self.controller.summary(session) if session else {}
            if info.get("pending"):
                if info.get("errors"):
                    code = f" (error {info['http_status']})" if info.get("http_status") else ""
                    kind, action = "retry", "Retry transcription"
                    text = (f"Transcription failed for {info['errors']} part(s){code}. "
                            "Check your API key and internet connection, then retry.")
                elif info.get("record_only"):
                    kind, action = "transcribe", "Transcribe now"
                    text = "This recording was saved without AI, so it has no transcript yet."
                else:
                    kind, action = "transcribe", "Transcribe now"
                    text = "Some of this recording has not been transcribed yet."
            elif info.get("processing") in ("notes_failed", "failed"):
                kind, action = "retry", "Retry notes"
                text = "Your transcript is ready, but the meeting notes could not be generated. Retry to create them."
            elif self.ai_state() == "key":
                kind, action = "key", "Set API key…"
                text = "Add your Gemini API key to get transcripts and notes. Until then, recordings are saved as audio only."
        self.notice_kind = kind
        self.notice.setVisible(bool(kind))
        self.notice_text.setText(text)
        self.notice_button.setText(action)
        self.notice_button.setVisible(bool(action))

    def notice_action(self) -> None:
        if self.notice_kind == "key":
            self.set_api_key()
        elif self.notice_kind in ("transcribe", "retry"):
            self.transcribe_selected()

    def transcribe_selected(self) -> None:
        session = self.effective_session()
        if session is None or self.status in ACTIVE or self.controller.transcribing():
            return
        if not self.has_ai_credentials() and not self.prompt_for_api_key():
            return
        try:
            self.controller.transcribe(session, self.env_file)
        except Exception as exc:
            self.message.setText(str(exc))
            return
        self.transcribe_started = True
        self.message.setText("Transcribing…")
        self.refresh()

    def load_devices(self) -> None:
        if not self.quitting:
            self.device_loader.start()

    def apply_devices(self, found: list) -> None:
        if self.quitting:
            return
        for device in found:
            combo = self.system if device["loopback"] else self.mic
            combo.addItem(f"{device['index']} · {device['name']}", device["index"])

    def devices_failed(self, text: str) -> None:
        if not self.quitting:
            self.message.setText(text)

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
        if self.ai_state() == "key":
            self.restore()
            if not self.prompt_for_api_key():
                self.message.setText("No API key yet. Add one for transcripts, or tick “Save audio only”.")
                return
        values = self.ai_values()
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
            self.popup.mark_stopping()

    def show_popup(self, force: bool = False) -> None:
        if self.quitting:
            return
        if force:
            self.popup_dismissed = False
            if not self.isMinimized():
                self.showMinimized()
        if self.isMinimized() and not self.popup_dismissed:
            if not self.popup.isVisible():
                self.popup.place(self.screen() or QApplication.primaryScreen())
            self.popup.show()

    def dismiss_popup(self) -> None:
        self.popup_dismissed = True
        self.popup.hide()

    def restore(self) -> None:
        self.popup.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "popup"):
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            if self.isMinimized() and not was_minimized:
                self.popup_dismissed = False
                self.show_popup()
            elif not self.isMinimized():
                self.popup.hide()
        super().changeEvent(event)

    def confirm_quit(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("AI Note Taker")
        box.setText("Stop recording and quit?")
        box.setInformativeText("Closing AI Note Taker will stop the recording. Your audio is saved and the "
                               "transcript and notes finish in the background.")
        quit_button = box.addButton("Stop && Quit", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is quit_button

    def request_quit(self) -> bool:
        if self.status in ("starting", "recording", "paused"):
            if not self.confirm_quit():
                return False
            try:
                current = self.controller.snapshot()["status"]
            except (OSError, ValueError, KeyError):
                current = self.status
            if current in ("starting", "recording", "paused"):
                self.controller.stop()
        self.quit_app()
        return True

    def closeEvent(self, event) -> None:
        if self.quitting:
            event.accept()
            return
        event.ignore()
        self.request_quit()

    def quit_app(self) -> None:
        self.quitting = True
        self.timer.stop()
        # The hidden worker finishes saving/AI requests even after the UI exits.
        self.controller.stop()
        self.popup.dispose()
        self.tray.hide()
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
        if self.quitting:
            return
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
        transcribing = self.controller.transcribing()
        announcement = ""
        if self.transcribe_started and not transcribing:
            self.transcribe_started = False
            announcement = ("Transcript ready." if self.controller.transcribe_returncode() == 0 else
                            "Transcription did not finish. Use the button above to retry.")
        self.record_button.setEnabled(not busy and not transcribing)
        self.pause_button.setEnabled(self.status in ("recording", "paused"))
        self.pause_button.setText("Resume" if self.status == "paused" else "Pause")
        self.stop_button.setEnabled(self.status in ("recording", "paused", "starting"))
        for widget in (self.system, self.microphone, self.offline, self.config_button, self.key_button, self.language):
            widget.setEnabled(not busy)
        self.mic.setEnabled(not busy and self.microphone.isChecked())
        self.source_label.setText("SYSTEM + MIC" if state.get("config", {}).get("microphone") else "SYSTEM AUDIO")
        messages = {"recording": "Listening to system audio. New transcript chunks appear about every 20 seconds.",
                    "paused": "Recording paused. Audio during this pause is discarded.",
                    "processing": "Audio saved. Gemini is finishing your transcript and meeting notes…",
                    "starting": "Opening the audio devices…",
                    "stopped": "Recording saved.",
                    "interrupted": "Recording was interrupted. Saved audio can be recovered with the CLI.",
                    "failed": state.get("error", f"Recorder failed ({state.get('error_type', 'unknown')}). See recorder.log.")}
        if self.status in messages:
            message = messages[self.status]
            processing = state.get("processing", {})
            if self.status == "stopped" and processing.get("status") == "complete":
                message = "Recording saved. Transcript and meeting notes are ready."
            elif state.get("config", {}).get("record_only"):
                message = "Recording audio locally. AI is disabled." if self.status == "recording" else message
            self.message.setText(message)
        if announcement:
            self.message.setText(announcement)
        self.waveform.level = max(state.get("levels", {}).values(), default=0) if self.status == "recording" else 0
        self.waveform.phase += 1
        self.waveform.update()
        self.popup.update_state("processing" if transcribing else self.status, clock)
        self.update_chip()
        self.update_notice(busy)
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


if __name__ == "__main__":
    main()
