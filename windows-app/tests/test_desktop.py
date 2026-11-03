import array
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

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
        # Never touch the real user's app settings (Windows registry) from tests.
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, cls.settings_dir.name)

    @classmethod
    def tearDownClass(cls):
        QSettings.setDefaultFormat(QSettings.Format.NativeFormat)
        cls.settings_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Hermetic environment: temp per-user config folder, no ambient key, explicit test key file.
        environment = patch.dict(os.environ, {"APPDATA": str(Path(self.temp.name) / "appdata")})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("GEMINI_API_KEY", None)
        QSettings("AI Note Taker", "Desktop").clear()        # no preferences leaking between tests
        self.env_path = Path(self.temp.name) / "test.env"
        self.env_path.write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
        self.controller = Mock()
        self.controller.output = Path(self.temp.name)
        self.controller.session = None
        self.controller.snapshot.return_value = {"status": "idle"}
        self.controller.sessions.return_value = []
        self.controller.effective_session.return_value = None
        self.controller.summary.return_value = {"chunks": 0, "transcribed": 0, "pending": 0,
                                                "errors": 0, "record_only": False}
        self.controller.transcribing.return_value = False
        self.controller.transcribe_returncode.return_value = None
        self.controller.transcript.return_value = ""
        self.controller.notes.return_value = ""
        self.window = self.make_window()
        self.addCleanup(self.dispose)

    def make_window(self):
        with patch("ai_note_taker.desktop.DesktopController", return_value=self.controller), \
                patch("ai_note_taker.desktop.list_devices", return_value=[]):
            window = MainWindow(Path(self.temp.name), self.env_path)
        window.timer.stop()
        return window

    def dispose(self):
        self.window.quitting = True
        self.window.timer.stop()
        if isValid(self.window.popup):
            self.window.popup.dispose()
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QApplication.processEvents()

    def close_window(self):
        with patch("ai_note_taker.desktop.QApplication.quit") as quit_app:
            self.window.close()
        return quit_app

    def wait_for(self, condition, seconds: float = 3.0) -> bool:
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return condition()

    def test_devices_load_in_the_background_and_fill_the_pickers(self):
        found = [{"index": 3, "name": "Speakers [Loopback]", "loopback": True},
                 {"index": 7, "name": "Headset Mic", "loopback": False}]
        with patch("ai_note_taker.desktop.list_devices", return_value=found):
            self.window.load_devices()
            self.assertTrue(self.wait_for(lambda: self.window.system.count() == 2))
        self.assertEqual(self.window.system.itemData(1), 3)
        self.assertEqual(self.window.mic.itemData(1), 7)

    def test_stuck_audio_service_never_blocks_the_window(self):
        import threading

        release = threading.Event()

        def hang(timeout):
            release.wait(5)
            raise TimeoutError

        with patch("ai_note_taker.desktop.list_devices", side_effect=hang):
            self.window.load_devices()          # must return immediately even though listing hangs
            self.assertTrue(self.window.isEnabled())
            self.window.refresh()               # UI keeps working meanwhile
            release.set()
            self.assertTrue(self.wait_for(lambda: "Windows Audio is not answering" in self.window.message.text()))
        self.assertEqual(self.window.system.count(), 1)   # default entry only

    def test_device_listing_child_process_is_killed_on_timeout(self):
        import subprocess

        from ai_note_taker import desktop_controller

        with patch("ai_note_taker.desktop_controller.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("devices", 1)):
            with self.assertRaises(TimeoutError):
                desktop_controller.list_devices(timeout=1)
        completed = Mock(returncode=0, stdout='[{"index": 1, "name": "x", "loopback": false}]')
        with patch("ai_note_taker.desktop_controller.subprocess.run", return_value=completed):
            self.assertEqual(desktop_controller.list_devices()[0]["index"], 1)

    # --- API key, AI status and "why is there no transcript?" -------------------------------

    def no_key(self, appdata: str):
        """Per-user config in a temp APPDATA and no key anywhere in the environment."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {"APPDATA": appdata}))
        os.environ.pop("GEMINI_API_KEY", None)
        self.window.env_file = None
        self.window.preferences.remove("env_file")
        self.window.offline.setChecked(False)
        self.addCleanup(stack.close)
        return stack

    def with_key(self) -> None:
        os.environ["GEMINI_API_KEY"] = "k"
        self.addCleanup(os.environ.pop, "GEMINI_API_KEY", None)

    def test_save_api_key_creates_updates_and_validates(self):
        from ai_note_taker import desktop

        with tempfile.TemporaryDirectory() as appdata, patch.dict(os.environ, {"APPDATA": appdata}):
            path = desktop.save_api_key("  key-one  ")
            self.assertEqual(path, Path(appdata) / "AI Note Taker" / ".env")
            text = path.read_text(encoding="utf-8")
            self.assertIn("GEMINI_API_KEY=key-one\n", text)
            self.assertIn("GEMINI_MODEL=gemini-2.5-flash", text)          # template settings kept
            path.write_text(text + "EXTRA=1\n", encoding="utf-8")
            desktop.save_api_key("key-two")
            updated = path.read_text(encoding="utf-8")
            self.assertEqual(updated.count("GEMINI_API_KEY="), 1)
            self.assertIn("GEMINI_API_KEY=key-two", updated)
            self.assertIn("EXTRA=1", updated)
            for bad in ("", "   ", "two words", "key\nINJECTED=1"):
                with self.assertRaises(ValueError):
                    desktop.save_api_key(bad)

    def test_blank_template_key_does_not_override_a_real_environment_key(self):
        blank = Path(self.temp.name) / "blank.env"
        blank.write_text("GEMINI_API_KEY=\n", encoding="utf-8")
        self.window.env_file = blank
        self.assertFalse(self.window.has_ai_credentials())
        os.environ["GEMINI_API_KEY"] = "from-environment"
        self.addCleanup(os.environ.pop, "GEMINI_API_KEY", None)
        self.assertTrue(self.window.has_ai_credentials())

    def test_user_config_is_discovered_by_a_later_launch(self):
        from ai_note_taker import desktop

        with tempfile.TemporaryDirectory() as appdata, patch.dict(os.environ, {"APPDATA": appdata}):
            desktop.save_api_key("abc")
            self.window.preferences.remove("env_file")
            self.assertEqual(self.window.find_config(), Path(appdata) / "AI Note Taker" / ".env")

    def test_pressing_record_without_a_key_asks_for_it_then_records(self):
        with tempfile.TemporaryDirectory() as appdata:
            self.no_key(appdata)
            with patch("ai_note_taker.desktop.QInputDialog.getText", return_value=("secret-key", True)) as ask:
                self.window.record()
            ask.assert_called_once()
            self.controller.start.assert_called_once()
            config, env_file = self.controller.start.call_args.args
            self.assertFalse(config["record_only"])
            self.assertEqual(Path(env_file), Path(appdata) / "AI Note Taker" / ".env")
            self.assertIn("GEMINI_API_KEY=secret-key", Path(env_file).read_text(encoding="utf-8"))

    def test_cancelling_the_key_prompt_does_not_record_and_explains(self):
        with tempfile.TemporaryDirectory() as appdata:
            self.no_key(appdata)
            with patch("ai_note_taker.desktop.QInputDialog.getText", return_value=("", False)):
                self.window.record()
            self.controller.start.assert_not_called()
            self.assertIn("API key", self.window.message.text())

    def test_ai_chip_says_whether_a_transcript_will_be_made(self):
        with tempfile.TemporaryDirectory() as appdata:
            self.no_key(appdata)
            self.window.update_chip()
            self.assertEqual(self.window.ai_chip.text(), "AI KEY NEEDED")
            self.assertEqual(self.window.ai_chip.property("tone"), "warn")
            self.window.offline.setChecked(True)
            self.assertEqual(self.window.ai_chip.text(), "AUDIO ONLY · NO TRANSCRIPT")
            self.assertEqual(self.window.ai_chip.property("tone"), "off")
            os.environ["GEMINI_API_KEY"] = "k"
            self.window.offline.setChecked(False)
            self.assertEqual(self.window.ai_chip.text(), "AI TRANSCRIPT ON")
            self.assertEqual(self.window.ai_chip.property("tone"), "ok")

    def test_audio_only_recording_offers_transcribe_now(self):
        session = Path(self.temp.name) / "20261103-120000-abcd1234"
        self.controller.effective_session.return_value = session
        self.controller.summary.return_value = {"pending": 3, "errors": 0, "record_only": True}
        self.controller.snapshot.return_value = {"status": "stopped", "config": {"record_only": True}}
        self.with_key()
        self.window.refresh()
        self.assertFalse(self.window.notice.isHidden())
        self.assertIn("saved without AI", self.window.notice_text.text())
        self.assertEqual(self.window.notice_button.text(), "Transcribe now")
        self.assertEqual(self.window.message.text(), "Recording saved.")   # the notice carries the detail
        self.window.notice_button.click()
        self.controller.transcribe.assert_called_once_with(session, self.window.env_file)

    def test_transcribe_now_asks_for_a_key_first_when_missing(self):
        with tempfile.TemporaryDirectory() as appdata:
            self.no_key(appdata)
            self.controller.effective_session.return_value = Path(self.temp.name) / "s"
            self.controller.summary.return_value = {"pending": 1, "errors": 0, "record_only": True}
            self.controller.snapshot.return_value = {"status": "stopped"}
            self.window.refresh()
            with patch("ai_note_taker.desktop.QInputDialog.getText", return_value=("", False)):
                self.window.notice_button.click()
            self.controller.transcribe.assert_not_called()

    def test_failed_transcription_is_visible_with_a_retry(self):
        self.controller.effective_session.return_value = Path(self.temp.name) / "s"
        self.controller.summary.return_value = {"pending": 2, "errors": 2, "http_status": 400,
                                                "error_type": "ClientError", "record_only": False}
        self.controller.snapshot.return_value = {"status": "stopped"}
        self.with_key()
        self.window.refresh()
        self.assertIn("failed for 2 part(s) (error 400)", self.window.notice_text.text())
        self.assertEqual(self.window.notice_button.text(), "Retry transcription")

    def test_notice_reports_progress_and_blocks_recording_while_transcribing(self):
        self.controller.transcribing.return_value = True
        self.controller.snapshot.return_value = {"status": "stopped"}
        self.window.refresh()
        self.assertIn("Transcribing", self.window.notice_text.text())
        self.assertTrue(self.window.notice_button.isHidden())
        self.assertFalse(self.window.record_button.isEnabled())
        self.assertFalse(self.window.popup.start_button.isEnabled())

    def test_finished_transcription_is_announced(self):
        self.window.transcribe_started = True
        self.controller.transcribe_returncode.return_value = 0
        self.controller.snapshot.return_value = {"status": "stopped"}
        self.window.refresh()
        self.assertEqual(self.window.message.text(), "Transcript ready.")
        self.assertFalse(self.window.transcribe_started)

    def test_failed_notes_offer_a_retry_even_though_the_transcript_exists(self):
        self.controller.effective_session.return_value = Path(self.temp.name) / "s"
        self.controller.summary.return_value = {"pending": 0, "errors": 0, "record_only": False,
                                                "processing": "notes_failed"}
        self.controller.snapshot.return_value = {"status": "stopped"}
        self.with_key()
        self.window.refresh()
        self.assertIn("meeting notes could not be generated", self.window.notice_text.text())
        self.assertEqual(self.window.notice_button.text(), "Retry notes")

    def test_no_notice_when_everything_is_transcribed(self):
        self.with_key()
        self.controller.effective_session.return_value = Path(self.temp.name) / "s"
        self.controller.snapshot.return_value = {"status": "stopped"}
        self.window.refresh()
        self.assertTrue(self.window.notice.isHidden())

    # --- dark mode ---------------------------------------------------------------------------

    def pick_theme(self, preference: str) -> None:
        self.window.theme_box.setCurrentIndex(self.window.theme_box.findData(preference))

    def test_theme_defaults_to_matching_windows(self):
        from ai_note_taker.theme import PREFERENCE_LABELS

        self.assertEqual(self.window.theme_preference, "system")
        self.assertEqual(self.window.theme_box.currentText(), PREFERENCE_LABELS["system"])
        self.assertEqual(self.window.theme_box.count(), 3)

    def test_choosing_dark_restyles_the_window_pill_waveform_and_tray_menu(self):
        from ai_note_taker import theme

        self.addCleanup(theme.apply_color_scheme, "system")
        self.pick_theme("dark")
        self.assertEqual(self.window.theme_name, "dark")
        self.assertIn(theme.DARK["bg"], self.window.styleSheet())
        self.assertIn(theme.DARK["bg"], self.window.tray_menu.styleSheet())
        self.assertEqual(self.window.popup.theme, "dark")
        self.assertEqual(self.window.waveform.active_color.name(), theme.DARK["wave_active"])
        self.assertEqual(self.window.preferences.value("theme"), "dark")
        self.pick_theme("light")
        self.assertEqual(self.window.theme_name, "light")
        self.assertIn(theme.LIGHT["bg"], self.window.styleSheet())
        self.assertEqual(self.window.popup.theme, "light")

    def test_the_theme_choice_is_remembered_across_launches(self):
        from ai_note_taker import theme

        self.addCleanup(theme.apply_color_scheme, "system")
        self.pick_theme("dark")
        second = self.make_window()
        self.addCleanup(lambda: (second.popup.dispose() if isValid(second.popup) else None,
                                 second.tray.hide(), second.deleteLater()))
        self.assertEqual(second.theme_preference, "dark")
        self.assertEqual(second.theme_box.currentData(), "dark")
        self.assertIn(theme.DARK["bg"], second.styleSheet())

    def test_match_windows_follows_the_system_live_but_an_explicit_choice_does_not(self):
        from ai_note_taker import theme

        self.addCleanup(theme.apply_color_scheme, "system")
        with patch("ai_note_taker.theme.system_is_dark", return_value=True):
            self.pick_theme("system")
            self.window.on_color_scheme_changed()
            self.assertEqual(self.window.theme_name, "dark")
        with patch("ai_note_taker.theme.system_is_dark", return_value=False):
            self.window.on_color_scheme_changed()                 # Windows switched to light
            self.assertEqual(self.window.theme_name, "light")
            self.assertEqual(self.window.popup.theme, "light")
        self.pick_theme("dark")
        with patch("ai_note_taker.theme.system_is_dark", return_value=False):
            self.window.on_color_scheme_changed()
            self.assertEqual(self.window.theme_name, "dark")      # explicit choice wins

    def test_unknown_saved_theme_falls_back_to_matching_windows(self):
        self.window.preferences.setValue("theme", "purple")
        broken = self.make_window()
        self.addCleanup(lambda: (broken.popup.dispose() if isValid(broken.popup) else None,
                                 broken.tray.hide(), broken.deleteLater()))
        self.assertEqual(broken.theme_preference, "system")

    def test_dark_dialogs_are_dark_and_stay_readable(self):
        from PySide6.QtWidgets import QMessageBox

        from ai_note_taker import theme

        self.addCleanup(theme.apply_color_scheme, "system")
        self.pick_theme("dark")
        box = QMessageBox(self.window)
        box.setText("Stop recording and quit?")
        box.show()
        QApplication.processEvents()
        self.addCleanup(box.close)
        self.assertEqual(box.grab().toImage().pixelColor(3, 3).name(), theme.DARK["bg"])

    def test_dialogs_use_the_light_app_background(self):
        # Regression: on Windows dark mode the quit prompt text (#292724) was unreadable
        # because only the text colour came from the app stylesheet, not the background.
        from PySide6.QtWidgets import QMessageBox

        from ai_note_taker import theme

        self.addCleanup(theme.apply_color_scheme, "system")
        self.window.theme_box.setCurrentIndex(self.window.theme_box.findData("light"))
        box = QMessageBox(self.window)
        box.setText("Stop recording and quit?")
        box.show()
        QApplication.processEvents()
        self.addCleanup(box.close)
        pixel = box.grab().toImage().pixelColor(3, 3)
        self.assertEqual(pixel.name(), theme.LIGHT["bg"])

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
        self.window.popup.pause_button.click()
        self.controller.resume.assert_called_once()

    def test_close_when_idle_quits_without_showing_popup(self):
        quit_app = self.close_window()
        quit_app.assert_called_once()
        self.assertTrue(self.window.quitting)
        self.assertFalse(self.window.popup.isVisible())
        self.assertFalse(self.window.tray.isVisible())

    def test_close_never_shows_popup(self):
        self.window.show()
        QApplication.processEvents()
        self.close_window()
        self.assertFalse(self.window.popup.isVisible())

    def test_close_while_recording_respects_confirmation(self):
        self.controller.snapshot.return_value = {"status": "recording", "active_seconds": 3}
        self.window.refresh()
        with patch.object(self.window, "confirm_quit", return_value=False) as confirm:
            quit_app = self.close_window()
        confirm.assert_called_once()
        self.controller.stop.assert_not_called()
        quit_app.assert_not_called()
        self.assertFalse(self.window.quitting)
        with patch.object(self.window, "confirm_quit", return_value=True):
            quit_app = self.close_window()
        self.controller.stop.assert_called()
        quit_app.assert_called_once()
        self.assertTrue(self.window.quitting)

    def test_close_while_processing_quits_without_prompt(self):
        self.controller.snapshot.return_value = {"status": "processing"}
        self.window.refresh()
        with patch.object(self.window, "confirm_quit") as confirm:
            quit_app = self.close_window()
        confirm.assert_not_called()
        quit_app.assert_called_once()
        self.assertTrue(self.window.quitting)

    def test_quit_guards_refresh_and_popup(self):
        with patch("ai_note_taker.desktop.QApplication.quit"):
            self.window.quit_app()
        self.assertFalse(self.window.timer.isActive())
        self.controller.snapshot.reset_mock()
        self.window.refresh()
        self.controller.snapshot.assert_not_called()
        self.window.show_popup(force=True)
        self.assertFalse(self.window.popup.isVisible())

    def test_minimizing_shows_popup_and_restore_hides_it(self):
        self.window.show()
        self.window.showMinimized()
        QApplication.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.assertTrue(self.window.popup.isVisible())
        self.window.restore()
        QApplication.processEvents()
        self.assertFalse(self.window.popup.isVisible())
        self.assertFalse(self.window.isMinimized())

    def test_popup_close_dismisses_without_stopping(self):
        self.window.show()
        self.window.showMinimized()
        QApplication.processEvents()
        self.assertTrue(self.window.popup.isVisible())
        self.window.popup.close_button.click()
        QApplication.processEvents()
        self.assertFalse(self.window.popup.isVisible())
        self.assertTrue(self.window.popup_dismissed)
        self.controller.stop.assert_not_called()
        self.assertFalse(self.window.quitting)
        self.assertTrue(self.window.isVisible())
        self.window.refresh()
        QApplication.processEvents()
        self.assertFalse(self.window.popup.isVisible())
        self.window.show_popup(force=True)
        QApplication.processEvents()
        self.assertTrue(self.window.popup.isVisible())
        self.assertFalse(self.window.popup_dismissed)
        self.window.popup.close_button.click()
        self.window.restore()
        self.window.showMinimized()
        QApplication.processEvents()
        self.assertTrue(self.window.popup.isVisible())

    def test_refresh_forwards_state_to_popup(self):
        for status, start, pause, stop in (("recording", False, True, True), ("paused", False, True, True),
                                           ("idle", True, False, False)):
            self.controller.snapshot.return_value = {"status": status, "active_seconds": 75}
            self.window.refresh()
            popup = self.window.popup
            self.assertEqual(popup.status, self.window.status)
            self.assertEqual(popup.time_label.text(), self.window.clock.text())
            self.assertEqual(popup.time_label.text(), "01:15")
            self.assertEqual(popup.start_button.isEnabled(), start)
            self.assertEqual(popup.pause_button.isEnabled(), pause)
            self.assertEqual(popup.stop_button.isEnabled(), stop)

    def test_popup_buttons_invoke_window_actions(self):
        self.dispose()
        with patch.object(MainWindow, "record") as record, patch.object(MainWindow, "toggle_pause") as pause, \
                patch.object(MainWindow, "stop") as stop:
            self.window = self.make_window()
            self.window.popup.start_button.click()
            record.assert_called_once()
            self.controller.snapshot.return_value = {"status": "recording", "active_seconds": 1}
            self.window.refresh()
            self.window.popup.pause_button.click()
            pause.assert_called_once()
            self.window.popup.stop_button.click()
            stop.assert_called_once()

    def test_tray_menu_actions(self):
        actions = {a.text(): a for a in self.window.tray.contextMenu().actions()}
        self.assertIn("Show floating recorder", actions)
        self.assertIn("Quit", actions)
        with patch("ai_note_taker.desktop.QApplication.quit") as quit_app:
            actions["Quit"].trigger()
        quit_app.assert_called_once()
        self.assertTrue(self.window.quitting)

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
