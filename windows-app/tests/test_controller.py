import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_note_taker.desktop_controller import DesktopController, worker_command


def make_session(root: Path, name: str = "20261103-120000-abcd1234", *, chunks: int = 0, responses: int = 0,
                 errors: int = 0, record_only: bool = False) -> Path:
    session = root / name
    for folder in ("chunks", "responses", "errors"):
        (session / folder).mkdir(parents=True)
    for index in range(chunks):
        (session / "chunks" / f"system-{index:06}.json").write_text("{}", encoding="utf-8")
    for index in range(responses):
        (session / "responses" / f"system-{index:06}.json").write_text("{}", encoding="utf-8")
    for index in range(errors):
        (session / "errors" / f"system-{index:06}.json").write_text(
            json.dumps({"error_type": "ClientError", "http_status": 400}), encoding="utf-8")
    (session / "config.json").write_text(json.dumps({"record_only": record_only}), encoding="utf-8")
    return session


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.controller = DesktopController(self.root)

    def test_summary_of_an_audio_only_session_shows_missing_transcript(self):
        session = make_session(self.root, chunks=4, record_only=True)
        info = self.controller.summary(session)
        self.assertEqual((info["chunks"], info["transcribed"], info["pending"]), (4, 0, 4))
        self.assertTrue(info["record_only"])
        self.assertEqual(info["errors"], 0)
        (session / "processing.json").write_text('{"status": "notes_failed"}', encoding="utf-8")
        self.assertEqual(self.controller.summary(session)["processing"], "notes_failed")

    def test_summary_reports_partial_progress_and_the_first_error(self):
        session = make_session(self.root, chunks=5, responses=2, errors=3)
        info = self.controller.summary(session)
        self.assertEqual(info["pending"], 3)
        self.assertEqual((info["errors"], info["http_status"], info["error_type"]), (3, 400, "ClientError"))
        self.assertFalse(info["record_only"])

    def test_summary_of_a_complete_or_silent_session_has_nothing_pending(self):
        self.assertEqual(self.controller.summary(make_session(self.root, "a", chunks=3, responses=3))["pending"], 0)
        self.assertEqual(self.controller.summary(make_session(self.root, "b", chunks=0))["pending"], 0)

    def test_summary_tolerates_a_missing_or_unreadable_session(self):
        self.assertEqual(self.controller.summary(self.root / "nope")["pending"], 0)
        broken = make_session(self.root, "c", chunks=1)
        (broken / "config.json").write_text("{not json", encoding="utf-8")
        self.assertFalse(self.controller.summary(broken)["record_only"])

    def test_transcribe_runs_the_cli_in_a_hidden_process_with_the_chosen_key(self):
        session = make_session(self.root, chunks=2, record_only=True)
        env_file = self.root / "keys.env"
        env_file.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
        process = Mock()
        process.poll.return_value = None
        with patch("ai_note_taker.desktop_controller.subprocess.Popen", return_value=process) as popen:
            self.controller.transcribe(session, env_file)
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["transcribe", str(session)])
        self.assertEqual(command[command.index("--env-file") + 1], str(env_file))
        self.assertEqual(command[: len(worker_command())], worker_command())
        self.assertEqual(popen.call_args.kwargs["env"]["GEMINI_API_KEY"], "from-file")
        self.assertTrue(self.controller.transcribing())
        with self.assertRaises(RuntimeError):
            self.controller.transcribe(session, env_file)       # one at a time

    def test_transcribing_ends_with_the_process_and_exposes_its_exit_code(self):
        process = Mock()
        process.poll.return_value = 0
        process.returncode = 0
        self.controller.transcriber = process
        self.assertFalse(self.controller.transcribing())
        self.assertEqual(self.controller.transcribe_returncode(), 0)

    def test_blank_settings_lines_do_not_hide_a_real_key_from_the_worker(self):
        env_file = self.root / "template.env"
        env_file.write_text("GEMINI_API_KEY=\nGEMINI_MODEL=gemini-2.5-flash\n", encoding="utf-8")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "real-key"}):
            environment = DesktopController._environment(env_file)
        self.assertEqual(environment["GEMINI_API_KEY"], "real-key")
        self.assertEqual(environment["GEMINI_MODEL"], "gemini-2.5-flash")

    def test_child_environment_does_not_leak_the_key_back_into_the_gui_process(self):
        env_file = self.root / "keys.env"
        env_file.write_text("GEMINI_API_KEY=only-for-worker\n", encoding="utf-8")
        os.environ.pop("GEMINI_API_KEY", None)
        environment = DesktopController._environment(env_file)
        self.assertEqual(environment["GEMINI_API_KEY"], "only-for-worker")
        self.assertNotIn("GEMINI_API_KEY", os.environ)


class DeleteTests(unittest.TestCase):
    """Deleting destroys user data, so the safety rules are tested explicitly."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.controller = DesktopController(self.root)
        # Never touch the real Recycle Bin from tests: stand in with a plain delete.
        patcher = patch("ai_note_taker.desktop_controller.send_to_recycle_bin", side_effect=shutil.rmtree)
        self.recycle = patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self, name: str, **state) -> Path:
        session = make_session(self.root, name, chunks=2, responses=2)
        (session / "session.json").write_text(json.dumps({"status": "stopped", "active_seconds": 65, **state}),
                                              encoding="utf-8")
        (session / "transcript.txt").write_text("hello", encoding="utf-8")
        (session / "notes.md").write_text("# notes", encoding="utf-8")
        return session

    def test_delete_removes_the_whole_recording_through_the_recycle_bin(self):
        session = self.saved("20261103-100000-aaaa0001")
        self.controller.delete_session(session)
        self.assertFalse(session.exists())
        self.recycle.assert_called_once_with(session.resolve())

    def test_delete_repoints_the_current_session_to_the_newest_remaining(self):
        old = self.saved("20261103-100000-aaaa0001")
        new = self.saved("20261103-110000-aaaa0002")
        (self.root / "current.json").write_text(json.dumps({"session_id": new.name}), encoding="utf-8")
        self.controller.session = new
        self.controller.delete_session(new)
        self.assertIsNone(self.controller.session)
        self.assertEqual(json.loads((self.root / "current.json").read_text())["session_id"], old.name)

    def test_deleting_the_last_recording_clears_the_current_pointer(self):
        only = self.saved("20261103-100000-aaaa0001")
        (self.root / "current.json").write_text(json.dumps({"session_id": only.name}), encoding="utf-8")
        self.controller.delete_session(only)
        self.assertFalse((self.root / "current.json").exists())
        self.assertEqual(self.controller.sessions(), [])
        self.assertIsNone(self.controller.effective_session())

    def test_a_dangling_pointer_does_not_crash_the_app(self):
        from ai_note_taker.runtime import current

        (self.root / "current.json").write_text(json.dumps({"session_id": "gone"}), encoding="utf-8")
        self.assertIsNone(current(self.root))
        (self.root / "current.json").write_text("{broken", encoding="utf-8")
        self.assertIsNone(current(self.root))
        self.assertEqual(self.controller.snapshot()["status"], "idle")

    def test_it_refuses_folders_outside_the_recordings_folder(self):
        outside = Path(self.temp.name).parent / "not-recordings"
        outside.mkdir(exist_ok=True)
        self.addCleanup(outside.rmdir)
        (outside / "session.json").write_text("{}", encoding="utf-8")
        self.addCleanup((outside / "session.json").unlink)
        for target in (outside, self.root, self.root / "nested" / "deeper"):
            self.assertFalse(self.controller.can_delete(target), str(target))
            with self.assertRaises(RuntimeError):
                self.controller.delete_session(target)
        self.recycle.assert_not_called()

    def test_it_refuses_folders_that_are_not_recording_sessions(self):
        stranger = self.root / "my-holiday-photos"
        stranger.mkdir()
        (stranger / "photo.jpg").write_text("x", encoding="utf-8")
        self.assertFalse(self.controller.can_delete(stranger))
        with self.assertRaises(RuntimeError):
            self.controller.delete_session(stranger)
        self.assertTrue((stranger / "photo.jpg").exists())

    def test_it_never_deletes_a_recording_that_is_still_running(self):
        live = self.saved("20261103-100000-aaaa0001", status="recording")
        with patch("ai_note_taker.desktop_controller.is_alive", return_value=True):
            self.assertFalse(self.controller.can_delete(live))
            with self.assertRaises(RuntimeError):
                self.controller.delete_session(live)
        self.assertTrue(live.exists())
        # A crashed recorder (process gone) is just leftover audio and can be deleted.
        with patch("ai_note_taker.desktop_controller.is_alive", return_value=False):
            self.assertTrue(self.controller.can_delete(live))

    def test_it_never_deletes_the_recording_being_transcribed(self):
        busy = self.saved("20261103-100000-aaaa0001")
        process = Mock()
        process.poll.return_value = None
        self.controller.transcriber, self.controller.transcribing_session = process, busy
        self.assertFalse(self.controller.can_delete(busy))
        other = self.saved("20261103-110000-aaaa0002")
        self.assertTrue(self.controller.can_delete(other))

    def test_describe_reports_length_and_what_is_saved(self):
        session = self.saved("20261103-100000-aaaa0001")
        facts = self.controller.describe(session)
        self.assertEqual(facts["seconds"], 65)
        self.assertTrue(facts["has_transcript"])
        self.assertTrue(facts["has_notes"])
        bare = make_session(self.root, "20261103-120000-aaaa0003", chunks=1)
        (bare / "session.json").write_text("{}", encoding="utf-8")
        facts = self.controller.describe(bare)
        self.assertFalse(facts["has_transcript"])
        self.assertFalse(facts["has_notes"])

    def test_a_failed_recycle_leaves_the_recording_and_the_pointer_alone(self):
        session = self.saved("20261103-100000-aaaa0001")
        (self.root / "current.json").write_text(json.dumps({"session_id": session.name}), encoding="utf-8")
        self.recycle.side_effect = OSError("Windows could not move the recording")
        with self.assertRaises(OSError):
            self.controller.delete_session(session)
        self.assertTrue(session.exists())
        self.assertTrue((self.root / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
