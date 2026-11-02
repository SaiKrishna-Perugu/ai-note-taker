import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from ai_note_taker.ai import Gemini
from ai_note_taker.audio import ChunkWriter
from ai_note_taker.models import Transcript
from ai_note_taker.processing import Processor, recover_chunks
from ai_note_taker.runtime import create_session, is_alive, recorder_lock
from ai_note_taker.storage import export_transcript, read_json, timestamp


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = create_session(self.root)

    def chunk(self, source="system", seconds=1, start=0):
        writer = ChunkWriter(self.session, source, 8000, 1, 1)
        writer.write(b"\x01\x00" * int(8000 * seconds), start)
        writer.close()

    def ai(self):
        ai = Mock()
        ai.transcribe.return_value = {
            "transcript": {"segments": [{"start": 0.1, "end": 0.8,
                                          "speaker": "Speaker 1", "text": "Hello"}]},
            "raw_response": '{"segments": []}', "model": "test"}
        ai.summarize.return_value = {
            "title": "Meeting", "summary": "A greeting", "key_points": ["Greeting"],
            "decisions": [], "action_items": [{"task": "Follow up", "owner": None,
                                               "due_date": None}], "open_questions": []}
        return ai

    def test_chunks_split_and_preserve_partial_tail(self):
        self.chunk(seconds=2.25, start=4)
        chunks = [read_json(path) for path in sorted((self.session / "chunks").glob("*.json"))]
        self.assertEqual([c["duration"] for c in chunks], [1, 1, .25])
        self.assertEqual([c["start"] for c in chunks], [4, 5, 6])
        for chunk in chunks:
            self.assertTrue(chunk["ready"])
            with wave.open(str(self.session / chunk["audio"]), "rb") as audio:
                self.assertEqual(audio.getnframes(), int(chunk["duration"] * 8000))

    def test_failure_retains_audio_and_retry_is_idempotent(self):
        self.chunk(start=30)
        ai = self.ai()
        ai.transcribe.side_effect = TimeoutError("sensitive URL must not be saved")
        processor = Processor(self.session, ai)
        self.assertEqual(processor.pass_once(force=True), 1)
        self.assertFalse(processor.finish())
        self.assertTrue((self.session / "audio/system-000000.wav").exists())
        error = read_json(self.session / "errors/system-000000.json")
        self.assertNotIn("sensitive", json.dumps(error))
        ai.transcribe.side_effect = None
        processor.pass_once(force=True)
        processor.pass_once(force=True)
        self.assertEqual(ai.transcribe.call_count, 2)
        self.assertTrue(processor.finish())
        transcript = read_json(self.session / "transcript.json")
        self.assertEqual(len(transcript["segments"]), 1)
        self.assertEqual(transcript["segments"][0]["start"], 30.1)
        self.assertIn("Hello", (self.session / "transcript.txt").read_text())
        self.assertTrue((self.session / "notes.md").exists())
        self.assertTrue((self.session / "notes.json").exists())
        self.assertFalse((self.session / "errors/system-000000.json").exists())

    def test_two_sources_merge_by_time_and_keep_speaker_scope(self):
        self.chunk(source="system", start=10)
        self.chunk(source="microphone", start=0)
        processor = Processor(self.session, self.ai())
        processor.pass_once()
        segments = read_json(self.session / "transcript.json")["segments"]
        self.assertEqual([s["source"] for s in segments], ["microphone", "system"])
        self.assertNotEqual(segments[0]["speaker_id"], segments[1]["speaker_id"])

    def test_open_wav_is_not_transcribed_and_can_be_recovered(self):
        writer = ChunkWriter(self.session, "system", 8000, 1, 30)
        writer.write(b"\x00\x00" * 8000, 0)
        # Simulate process death after WAV header updates, before publishing metadata.
        writer.stream.close()
        writer.stream = None
        ai = self.ai()
        processor = Processor(self.session, ai)
        processor.pass_once()
        ai.transcribe.assert_not_called()
        recover_chunks(self.session)
        processor.pass_once()
        ai.transcribe.assert_called_once()

    def test_empty_speech_does_not_hallucinate_notes(self):
        self.chunk()
        ai = self.ai()
        ai.transcribe.return_value["transcript"] = {"segments": []}
        processor = Processor(self.session, ai)
        processor.pass_once()
        self.assertTrue(processor.finish())
        ai.summarize.assert_not_called()
        self.assertEqual(read_json(self.session / "notes.json")["title"], "No speech detected")

    def test_notes_failure_preserves_transcript(self):
        self.chunk()
        ai = self.ai()
        ai.summarize.side_effect = TimeoutError()
        processor = Processor(self.session, ai)
        processor.pass_once()
        self.assertFalse(processor.finish())
        self.assertEqual(read_json(self.session / "processing.json")["status"], "notes_failed")
        self.assertTrue((self.session / "transcript.json").exists())

    def test_exports_can_be_rebuilt_after_interruption(self):
        self.chunk()
        Processor(self.session, self.ai()).pass_once()
        (self.session / "transcript.txt").unlink()
        export_transcript(self.session)
        self.assertIn("Hello", (self.session / "transcript.txt").read_text())

    def test_timestamp_carries_milliseconds(self):
        self.assertEqual(timestamp(59.9999), "00:01:00.000")

    def test_lock_prevents_concurrent_recorder(self):
        with recorder_lock(self.root):
            with self.assertRaises(RuntimeError):
                with recorder_lock(self.root):
                    self.fail("Second recorder acquired the lock")
        with recorder_lock(self.root):
            pass

    def test_stale_pid_is_not_reported_alive(self):
        import os

        self.assertFalse(is_alive({"pid": os.getpid(), "process_created": 0}))

    def test_invalid_ai_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            Transcript.model_validate({"segments": [
                {"start": 2, "end": 1, "speaker": "Speaker 1", "text": "Hi"}]})

    @patch.dict("os.environ", {"GEMINI_API_KEY": "fake-test-key"})
    @patch("ai_note_taker.ai.genai.Client")
    def test_gemini_audio_request_uses_schema_and_validates_timestamps(self, client):
        self.chunk()
        client.return_value.models.generate_content.return_value.text = json.dumps({"segments": [
            {"start": 0, "end": 15, "speaker": "Speaker 1", "text": "Hi"}]})
        ai = Gemini("test-model")
        with self.assertRaises(ValueError):
            ai.transcribe(self.session / "audio/system-000000.wav", 1, "auto")
        request = client.return_value.models.generate_content.call_args.kwargs
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["contents"][1].inline_data.mime_type, "audio/wav")


if __name__ == "__main__":
    unittest.main()
