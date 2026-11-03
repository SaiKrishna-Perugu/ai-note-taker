import array
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindowStateChangeEvent

from ai_note_taker.audio import Capture
from ai_note_taker.desktop import MainWindow
from ai_note_taker.runtime import create_session, run_session
from ai_note_taker.storage import read_json


class PauseTests(unittest.TestCase):
    def test_paused_packets_are_not_written_and_resume_uses_new_chunk(self):
        with tempfile.TemporaryDirectory() as folder:
            session = create_session(Path(folder))
            pa = Mock()
            manager = pa.PyAudio.return_value
            manager.get_default_wasapi_loopback.return_value = {
                "isLoopbackDevice": True, "index": 5, "defaultSampleRate": 8000,
                "maxInputChannels": 1, "name": "Test loopback"}
            capture = Capture(session, 20, False)
            with patch("ai_note_taker.audio.backend", return_value=pa):
                capture.start()
                callback = manager.open.call_args.kwargs["stream_callback"]
                callback(array.array("h", [1000] * 80).tobytes(), 80, {}, 0)
                capture.pause()
                callback(array.array("h", [30000] * 800).tobytes(), 800, {}, 0)
                capture.resume()
                callback(array.array("h", [2000] * 80).tobytes(), 80, {}, 0)
                capture.close()
            paths = sorted((session / "audio").glob("*.wav"))
            self.assertEqual(len(paths), 2)
            recorded = []
            for path in paths:
                with wave.open(str(path), "rb") as stream:
                    recorded.extend(array.array("h", stream.readframes(stream.getnframes())))
            self.assertEqual(len(recorded), 160)
            self.assertNotIn(30000, recorded)
            self.assertEqual(set(recorded), {1000, 2000})

    @patch("ai_note_taker.runtime.Capture")
    def test_runtime_acknowledges_pause_resume_and_records_intervals(self, capture_class):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = create_session(root)
            capture = capture_class.return_value
            capture.errors, capture.sources, capture.levels = [], [], {}
            calls = 0

            def advance():
                nonlocal calls
                calls += 1
                if calls == 1:
                    (session / "pause.request").touch()
                elif calls == 2:
                    self.assertEqual(read_json(session / "session.json")["status"], "paused")
                    (session / "pause.request").unlink()
                else:
                    (session / "stop.request").touch()

            capture.check.side_effect = advance
            run_session(root, session, {"record_only": True, "chunk_seconds": 20,
                                       "microphone": False, "system_device": None,
                                       "mic_device": None, "duration": None})
            capture.pause.assert_called_once()
            capture.resume.assert_called_once()
            state = read_json(session / "session.json")
            self.assertEqual(state["status"], "stopped")
            self.assertEqual(len(state["pause_intervals"]), 1)
            self.assertIsNotNone(state["pause_intervals"][0]["end"])


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = Mock()
        self.controller.output = Path(self.temp.name)
        self.controller.session = None
        self.controller.snapshot.return_value = {"status": "idle"}
        self.controller.sessions.return_value = []
        self.controller.transcript.return_value = ""
        self.controller.notes.return_value = ""
        with patch("ai_note_taker.desktop.DesktopController", return_value=self.controller):
            self.window = MainWindow(Path(self.temp.name))
        self.window.timer.stop()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.quitting = True
        self.window.mini.hide()
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def test_idle_and_active_buttons_match_state(self):
        self.assertTrue(self.window.record_button.isEnabled())
        self.assertFalse(self.window.pause_button.isEnabled())
        self.controller.snapshot.return_value = {"status": "recording", "active_seconds": 65}
        self.window.refresh()
        self.assertFalse(self.window.record_button.isEnabled())
        self.assertTrue(self.window.pause_button.isEnabled())
        self.assertEqual(self.window.clock.text(), "01:05")
        self.window.pause_button.click()
        self.controller.pause.assert_called_once()
        self.controller.snapshot.return_value = {"status": "paused", "active_seconds": 65}
        self.window.refresh()
        self.assertIn("Resume", self.window.pause_button.text())
        self.window.mini.toggle.click()
        self.controller.resume.assert_called_once()

    def test_minimizing_shows_mini_and_expand_restores_window(self):
        self.window.show()
        self.window.showMinimized()
        QApplication.processEvents()
        self.assertTrue(self.window.mini.isVisible())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        # Simulate restoring from the taskbar, without the app's restore helper.
        self.window.showNormal()
        QApplication.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.mini.isVisible())

    def test_mini_is_circular_and_opens_stacked_controls(self):
        self.window.show_mini()
        QApplication.processEvents()
        mini = self.window.mini
        self.assertEqual(mini.height(), mini.width())
        self.assertTrue(mini.bubble.isVisible())
        self.assertFalse(mini.panel.isVisible())
        mini.bubble.click()
        QApplication.processEvents()
        self.assertGreater(mini.height(), mini.width())
        self.assertTrue(mini.panel.isVisible())
        self.assertGreater(mini.stop.y(), mini.toggle.y())
        self.assertGreater(mini.expand.y(), mini.stop.y())
        mini.collapse_button.click()
        self.assertEqual(mini.size().width(), 80)
        self.assertTrue(mini.bubble.isVisible())

    def test_mini_close_hides_popup_without_stopping_or_reopening(self):
        self.window.show_mini()
        self.window.mini.show_controls()
        with patch.object(self.window.tray, "isVisible", return_value=True):
            self.window.mini.close_button.click()
        self.assertFalse(self.window.mini.isVisible())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        self.controller.stop.assert_not_called()
        self.window.refresh()
        QApplication.processEvents()
        self.assertFalse(self.window.mini.isVisible())
        self.window.restore()
        self.assertTrue(self.window.isVisible())

    def test_bubble_can_be_minimized_without_losing_taskbar_window(self):
        self.window.show_mini()
        self.window.mini.bubble_minimize.click()
        QApplication.processEvents()
        self.assertFalse(self.window.mini.isVisible())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMinimized())
        self.controller.stop.assert_not_called()
        self.window.refresh()
        QApplication.processEvents()
        self.assertFalse(self.window.mini.isVisible())
        self.window.showNormal()
        self.window.showMinimized()
        QApplication.processEvents()
        self.assertTrue(self.window.mini.bubble.isVisible())

    def test_expanded_popup_has_its_own_minimize(self):
        self.window.show_mini()
        self.window.mini.bubble.click()
        self.window.mini.minimize_button.click()
        QApplication.processEvents()
        self.assertFalse(self.window.mini.isVisible())
        self.assertTrue(self.window.isMinimized())

    def test_quick_minimize_restore_does_not_leave_stray_bubble(self):
        self.window.show()
        self.window.showMinimized()
        self.window.showNormal()
        QApplication.processEvents()
        self.assertFalse(self.window.mini.isVisible())

    def test_state_change_while_already_minimized_keeps_popup_dismissed(self):
        self.window.show_mini()
        self.window.minimize_popup()
        self.window.changeEvent(QWindowStateChangeEvent(Qt.WindowState.WindowMinimized))
        self.assertFalse(self.window.mini.isVisible())

    def test_mini_quit_stops_recording_and_exits(self):
        self.window.show_mini()
        self.window.mini.show_controls()
        with patch("ai_note_taker.desktop.QApplication.quit") as quit_app:
            self.window.mini.quit_button.click()
        self.controller.stop.assert_called_once()
        quit_app.assert_called_once()
        self.assertFalse(self.window.mini.isVisible())

    def test_record_and_stop_controls_send_configuration(self):
        self.window.offline.setChecked(True)
        self.controller.start.return_value = Path(self.temp.name) / "new-session"
        self.window.record_button.click()
        config = self.controller.start.call_args.args[0]
        self.assertTrue(config["record_only"])
        self.assertEqual(config["chunk_seconds"], 20)
        self.window.stop()
        self.controller.stop.assert_called_once()

    def test_new_meeting_clears_old_transcript(self):
        self.controller.transcript.return_value = "Old meeting text"
        self.window.refresh()
        self.assertIn("Old", self.window.transcript.toPlainText())
        self.window.new_meeting()
        self.window.refresh()
        self.assertEqual(self.window.transcript.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()
