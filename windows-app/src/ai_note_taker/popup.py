"""Notion-style floating recorder: a slim vertical capsule with stacked controls."""

from collections.abc import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeyEvent, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QToolButton, QVBoxLayout, QWidget

CREAM = QColor("#f8f5ef")
INK = QColor("#292724")
ACCENT = QColor("#be5b3d")
BORDER = QColor("#d7d0c3")
RECORD_RED = QColor("#d9382c")
HOVER = QColor("#ece6dc")
PRESSED = QColor("#e2dacb")

SHADOW_MARGIN = 10
CAPSULE_SIZE = QSize(56, 236)
WIDGET_SIZE = QSize(CAPSULE_SIZE.width() + 2 * SHADOW_MARGIN, CAPSULE_SIZE.height() + 2 * SHADOW_MARGIN)
EDGE_INSET = 24
SETTINGS_KEY = "popup_position"


class CapsuleButton(QToolButton):
    """Round tool button that paints its own icon (kind: close/start/pause/stop)."""

    def __init__(self, kind: str, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("kind", kind)
        self.setProperty("mode", "pause")
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAutoRaise(True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        bounds = QRectF(self.rect())
        circle = bounds.adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        if enabled and self.isDown():
            painter.setBrush(PRESSED)
            painter.drawEllipse(circle)
        elif enabled and self.underMouse():
            painter.setBrush(HOVER)
            painter.drawEllipse(circle)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            ring = QPen(ACCENT, 1.5)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle.adjusted(0.75, 0.75, -0.75, -0.75))
        painter.setOpacity(1.0 if enabled else 0.35)
        self._paint_icon(painter, bounds)
        painter.end()

    def _paint_icon(self, painter: QPainter, bounds: QRectF) -> None:
        kind = self.property("kind")
        centre = bounds.center()
        unit = min(bounds.width(), bounds.height())
        painter.setPen(Qt.PenStyle.NoPen)
        if kind == "start":
            radius = unit * 0.2
            painter.setBrush(RECORD_RED)
            painter.drawEllipse(centre, radius, radius)
        elif kind == "pause" and self.property("mode") == "resume":
            half = unit * 0.2
            path = QPainterPath()
            path.moveTo(centre.x() - half * 0.7, centre.y() - half * 1.15)
            path.lineTo(centre.x() + half * 1.2, centre.y())
            path.lineTo(centre.x() - half * 0.7, centre.y() + half * 1.15)
            path.closeSubpath()
            painter.setBrush(INK)
            painter.drawPath(path)
        elif kind == "pause":
            bar_w, bar_h, gap = unit * 0.12, unit * 0.38, unit * 0.1
            painter.setBrush(INK)
            for sign in (-1, 1):
                x = centre.x() + sign * (gap / 2 + bar_w / 2) - bar_w / 2
                painter.drawRoundedRect(QRectF(x, centre.y() - bar_h / 2, bar_w, bar_h), 1.5, 1.5)
        elif kind == "stop":
            side = unit * 0.34
            painter.setBrush(INK)
            painter.drawRoundedRect(QRectF(centre.x() - side / 2, centre.y() - side / 2, side, side), 3, 3)
        elif kind == "close":
            reach = unit * 0.17
            pen = QPen(INK, 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(centre.x() - reach, centre.y() - reach), QPointF(centre.x() + reach, centre.y() + reach))
            painter.drawLine(QPointF(centre.x() - reach, centre.y() + reach), QPointF(centre.x() + reach, centre.y() - reach))

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()


class CapsuleRecorder(QWidget):
    """Frameless always-on-top vertical capsule with close, start, pause/resume and stop."""

    def __init__(
        self,
        on_start: Callable[[], None],
        on_toggle_pause: Callable[[], None],
        on_stop: Callable[[], None],
        on_close: Callable[[], None],
        settings: QSettings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(None)
        self._on_start = on_start
        self._on_toggle_pause = on_toggle_pause
        self._on_stop = on_stop
        self._on_close = on_close
        self._settings = settings
        self._disposed = False
        self._press_global: QPoint | None = None
        self._press_origin: QPoint | None = None
        self._dragging = False
        self._anchor_screen: QScreen | None = None
        self.status = "idle"

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedSize(WIDGET_SIZE)
        self.setObjectName("capsule")
        self.setAccessibleName("Floating recorder")
        self.setWindowTitle("Floating recorder")

        self.close_button = CapsuleButton("close", 24)
        self.close_button.setObjectName("capsuleClose")
        self.close_button.setAccessibleName("Close floating recorder")
        self.close_button.setToolTip("Close floating recorder")
        self.start_button = CapsuleButton("start", 40)
        self.start_button.setObjectName("capsuleStart")
        self.start_button.setAccessibleName("Start recording")
        self.start_button.setToolTip("Start recording")
        self.pause_button = CapsuleButton("pause", 40)
        self.pause_button.setObjectName("capsulePause")
        self.pause_button.setAccessibleName("Pause recording")
        self.pause_button.setToolTip("Pause recording")
        self.stop_button = CapsuleButton("stop", 40)
        self.stop_button.setObjectName("capsuleStop")
        self.stop_button.setAccessibleName("Stop recording")
        self.stop_button.setToolTip("Stop recording")

        divider = QFrame()
        divider.setObjectName("capsuleDivider")
        divider.setFixedSize(26, 1)
        divider.setStyleSheet("#capsuleDivider { background: #e4ded2; border: none; }")

        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("capsuleTime")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        self.time_label.setFont(font)
        self.time_label.setStyleSheet("color: #292724; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SHADOW_MARGIN, SHADOW_MARGIN + 8, SHADOW_MARGIN, SHADOW_MARGIN + 10)
        layout.setSpacing(0)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        for button in (self.start_button, self.pause_button, self.stop_button):
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            layout.addSpacing(6)
        layout.addStretch(1)
        layout.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setTabOrder(self.close_button, self.start_button)
        self.setTabOrder(self.start_button, self.pause_button)
        self.setTabOrder(self.pause_button, self.stop_button)

        self.close_button.clicked.connect(lambda: self._on_close())
        self.start_button.clicked.connect(self._start_clicked)
        self.pause_button.clicked.connect(lambda: self._on_toggle_pause())
        self.stop_button.clicked.connect(self._stop_clicked)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenRemoved.connect(self._reclamp)

        self.update_state("idle", "00:00")

    # --- state -----------------------------------------------------------------

    def update_state(self, status: str, clock: str) -> None:
        self.status = status
        self.time_label.setText(clock)
        self.close_button.setEnabled(True)
        start_tip = "Start recording"
        pause_tip = "Pause recording"
        stop_tip = "Stop recording"
        mode = "pause"
        if status == "starting":
            start, pause, stop = False, False, True
            start_tip = "Starting…"
        elif status in ("recording", "paused"):
            start, pause, stop = False, True, True
            if status == "paused":
                mode = "resume"
                pause_tip = "Resume recording"
        elif status == "processing":
            start, pause, stop = False, False, False
            start_tip = pause_tip = stop_tip = "Finishing notes…"
        else:
            start, pause, stop = True, False, False
        self.start_button.setEnabled(start)
        self.start_button.setToolTip(start_tip)
        self.start_button.setAccessibleName("Start recording")
        self.pause_button.setProperty("mode", mode)
        self.pause_button.setEnabled(pause)
        self.pause_button.setToolTip(pause_tip)
        self.pause_button.setAccessibleName(pause_tip if pause else ("Resume recording" if mode == "resume" else "Pause recording"))
        self.stop_button.setEnabled(stop)
        self.stop_button.setToolTip(stop_tip)
        self.stop_button.setAccessibleName("Stop recording")
        for button in (self.start_button, self.pause_button, self.stop_button):
            button.update()

    def mark_stopping(self) -> None:
        self.stop_button.setEnabled(False)
        self.stop_button.update()

    def _start_clicked(self) -> None:
        self._on_start()
        self.start_button.setEnabled(False)
        self.start_button.update()

    def _stop_clicked(self) -> None:
        self.mark_stopping()
        self._on_stop()

    # --- placement -------------------------------------------------------------

    def place(self, anchor_screen: QScreen | None) -> None:
        self._anchor_screen = anchor_screen
        saved = self._saved_position()
        if saved is not None:
            self.move_clamped(saved)
            return
        screen = anchor_screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - EDGE_INSET - self.width() + 1 + SHADOW_MARGIN
        y = area.top() + (area.height() - self.height()) // 2
        self.move_clamped(QPoint(x, y))

    def _saved_position(self) -> QPoint | None:
        if self._settings is None:
            return None
        value = self._settings.value(SETTINGS_KEY)
        if isinstance(value, QPoint):
            point = value
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                point = QPoint(int(value[0]), int(value[1]))
            except (TypeError, ValueError):
                return None
        else:
            return None
        probe = QRect(point, self.size()).center()
        return point if QGuiApplication.screenAt(probe) is not None else None

    def move_clamped(self, point: QPoint) -> None:
        rect = QRect(point, self.size())
        screen = QGuiApplication.screenAt(rect.center()) or self._anchor_screen or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(point.x(), area.right() - self.width() + 1))
            y = max(area.top(), min(point.y(), area.bottom() - self.height() + 1))
            point = QPoint(x, y)
        self.move(point)

    def _reclamp(self, *_args) -> None:
        if not self._disposed:
            self.move_clamped(self.pos())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.move_clamped(self.pos())

    # --- painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(SHADOW_MARGIN, SHADOW_MARGIN, CAPSULE_SIZE.width(), CAPSULE_SIZE.height())
        painter.setPen(Qt.PenStyle.NoPen)
        steps = 6
        for i in range(steps, 0, -1):
            grow = float(i)
            ring = body.adjusted(-grow, -grow, grow, grow).translated(0, 2)
            colour = QColor(41, 39, 36, 11)
            painter.setBrush(colour)
            painter.drawRoundedRect(ring, ring.width() / 2, ring.width() / 2)
        painter.setPen(QPen(BORDER, 1))
        painter.setBrush(CREAM)
        inner = body.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(inner, inner.width() / 2, inner.width() / 2)
        painter.end()

    # --- input -----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or self._press_origin is None:
            super().mouseMoveEvent(event)
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True
        if self._dragging:
            self.move_clamped(self._press_origin + delta)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            if self._dragging and self._settings is not None:
                self._settings.setValue(SETTINGS_KEY, self.pos())
            self._press_global = None
            self._press_origin = None
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._on_close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._disposed:
            event.accept()
            return
        event.accept()
        self.hide()
        self._on_close()

    def dispose(self) -> None:
        """Hide and delete the capsule without invoking the close callback."""
        self._disposed = True
        self.hide()
        self.close()
        self.deleteLater()
