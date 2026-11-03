import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QSettings, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from ai_note_taker.popup import CapsuleRecorder

IDLE_STATES = ["idle", "stopped", "failed", "interrupted", "unknown"]


class CapsuleRecorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []
        self.temp = tempfile.TemporaryDirectory()
        self.on_start = Mock()
        self.on_toggle_pause = Mock()
        self.on_stop = Mock()
        self.on_close = Mock()

    def tearDown(self):
        for widget in self.widgets:
            widget.dispose()
        self.temp.cleanup()

    def make(self, settings=None):
        widget = CapsuleRecorder(self.on_start, self.on_toggle_pause, self.on_stop,
                                 self.on_close, settings=settings)
        self.widgets.append(widget)
        widget.show()
        QApplication.processEvents()
        return widget

    def settings_file(self, name="settings.ini"):
        return QSettings(str(Path(self.temp.name) / name), QSettings.Format.IniFormat)

    # Construction and object names

    def test_widget_has_fixed_capsule_size(self):
        widget = self.make()
        self.assertEqual(widget.size(), QSize(76, 256))

    def test_initial_status_is_idle(self):
        widget = self.make()
        self.assertEqual(widget.status, "idle")

    def test_child_controls_have_expected_object_names_and_types(self):
        widget = self.make()
        expected = {
            "capsuleClose": widget.close_button,
            "capsuleStart": widget.start_button,
            "capsulePause": widget.pause_button,
            "capsuleStop": widget.stop_button,
            "capsuleTime": widget.time_label,
        }
        for name, control in expected.items():
            with self.subTest(name=name):
                self.assertEqual(control.objectName(), name)
        for button in (widget.close_button, widget.start_button,
                       widget.pause_button, widget.stop_button):
            self.assertIsInstance(button, QToolButton)

    # State table

    def test_idle_like_states_enable_only_start(self):
        widget = self.make()
        for status in IDLE_STATES:
            with self.subTest(status=status):
                widget.update_state(status, "00:00")
                self.assertTrue(widget.start_button.isEnabled())
                self.assertFalse(widget.pause_button.isEnabled())
                self.assertFalse(widget.stop_button.isEnabled())

    def test_starting_enables_only_stop(self):
        widget = self.make()
        widget.update_state("starting", "00:00")
        self.assertFalse(widget.start_button.isEnabled())
        self.assertFalse(widget.pause_button.isEnabled())
        self.assertTrue(widget.stop_button.isEnabled())

    def test_recording_enables_pause_and_stop_with_pause_labels(self):
        widget = self.make()
        widget.update_state("recording", "00:12")
        self.assertFalse(widget.start_button.isEnabled())
        self.assertTrue(widget.pause_button.isEnabled())
        self.assertEqual(widget.pause_button.property("mode"), "pause")
        self.assertEqual(widget.pause_button.accessibleName(), "Pause recording")
        self.assertTrue(widget.stop_button.isEnabled())

    def test_paused_shows_resume_label_and_keeps_pause_enabled(self):
        widget = self.make()
        widget.update_state("paused", "00:30")
        self.assertTrue(widget.pause_button.isEnabled())
        self.assertEqual(widget.pause_button.property("mode"), "resume")
        self.assertEqual(widget.pause_button.accessibleName(), "Resume recording")

    def test_processing_disables_start_pause_and_stop(self):
        widget = self.make()
        widget.update_state("processing", "01:00")
        self.assertFalse(widget.start_button.isEnabled())
        self.assertFalse(widget.pause_button.isEnabled())
        self.assertFalse(widget.stop_button.isEnabled())

    def test_close_is_enabled_in_every_state(self):
        widget = self.make()
        for status in IDLE_STATES + ["starting", "recording", "paused", "processing"]:
            with self.subTest(status=status):
                widget.update_state(status, "00:00")
                self.assertTrue(widget.close_button.isEnabled())

    def test_update_state_sets_time_label_and_status(self):
        widget = self.make()
        widget.update_state("recording", "03:21")
        self.assertEqual(widget.time_label.text(), "03:21")
        self.assertEqual(widget.status, "recording")

    # Clicks

    def test_start_click_calls_on_start_once_and_disables_start(self):
        widget = self.make()
        widget.update_state("idle", "00:00")
        widget.start_button.click()
        self.on_start.assert_called_once()
        self.assertFalse(widget.start_button.isEnabled())

    def test_start_stays_disabled_until_next_update_state(self):
        widget = self.make()
        widget.update_state("idle", "00:00")
        widget.start_button.click()
        widget.start_button.click()
        self.on_start.assert_called_once()
        widget.update_state("idle", "00:00")
        self.assertTrue(widget.start_button.isEnabled())

    def test_stop_click_calls_on_stop_once_and_disables_stop(self):
        widget = self.make()
        widget.update_state("recording", "00:05")
        widget.stop_button.click()
        self.on_stop.assert_called_once()
        self.assertFalse(widget.stop_button.isEnabled())
        widget.stop_button.click()
        self.on_stop.assert_called_once()

    def test_pause_click_calls_on_toggle_pause(self):
        widget = self.make()
        widget.update_state("recording", "00:05")
        widget.pause_button.click()
        self.on_toggle_pause.assert_called_once()

    def test_close_click_calls_on_close(self):
        widget = self.make()
        widget.update_state("recording", "00:05")
        widget.close_button.click()
        self.on_close.assert_called_once()

    def test_escape_key_calls_on_close(self):
        widget = self.make()
        QTest.keyClick(widget, Qt.Key.Key_Escape)
        self.on_close.assert_called_once()

    def test_widget_close_calls_on_close_once(self):
        widget = self.make()
        widget.close()
        self.on_close.assert_called_once()

    def test_dispose_does_not_call_on_close(self):
        widget = self.make()
        widget.dispose()
        self.on_close.assert_not_called()

    # Clamping

    def test_move_clamped_keeps_widget_inside_primary_screen_for_far_off_screen_points(self):
        widget = self.make()
        available = QGuiApplication.primaryScreen().availableGeometry()
        points = [
            QPoint(-100000, -100000),
            QPoint(100000, 100000),
            QPoint(-100000, 100000),
            QPoint(100000, -100000),
            QPoint(available.left() - 5000, available.center().y()),
            QPoint(available.right() + 5000, available.center().y()),
        ]
        for point in points:
            with self.subTest(point=(point.x(), point.y())):
                widget.move_clamped(point)
                QApplication.processEvents()
                self.assertTrue(available.contains(widget.frameGeometry()),
                                f"{widget.frameGeometry()} not inside {available}")

    # Dragging and persistence

    def test_drag_release_saves_popup_position(self):
        settings = self.settings_file()
        widget = self.make(settings)
        widget.move_clamped(QPoint(100, 100))
        QTest.mousePress(widget, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        QTest.mouseMove(widget, QPoint(60, 60))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(60, 60))
        QApplication.processEvents()
        settings.sync()
        self.assertIsNotNone(settings.value("popup_position"))

    def test_place_restores_saved_position(self):
        settings = self.settings_file()
        first = self.make(settings)
        first.move_clamped(QPoint(150, 120))
        QTest.mousePress(first, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        QTest.mouseMove(first, QPoint(40, 40))
        QTest.mouseRelease(first, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(40, 40))
        QApplication.processEvents()
        settings.sync()
        saved_top_left = first.frameGeometry().topLeft()

        second = self.make(self.settings_file())
        second.place(QPoint(0, 0))
        QApplication.processEvents()
        self.assertEqual(second.frameGeometry().topLeft(), saved_top_left)

    def test_drag_release_without_settings_does_not_raise(self):
        widget = self.make(None)
        widget.move_clamped(QPoint(100, 100))
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
