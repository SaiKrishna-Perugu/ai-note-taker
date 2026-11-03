"""Disk-backed transcription queue, retry, and meeting exports."""

import logging
import time
import wave
from pathlib import Path

from ai_note_taker.ai import Gemini
from ai_note_taker.storage import export_notes, export_transcript, now, read_json, write_json

log = logging.getLogger(__name__)


class Processor:
    def __init__(self, session: Path, ai: Gemini, language: str = "auto") -> None:
        self.session, self.ai, self.language = session, ai, language
        self.retry_at: dict[str, float] = {}
        self.failures: dict[str, int] = {}

    def pass_once(self, force: bool = False) -> int:
        failed = 0
        changed = False
        for path in sorted((self.session / "chunks").glob("*.json")):
            chunk = read_json(path)
            result_path = self.session / "responses" / path.name
            if not chunk["ready"] or result_path.exists():
                continue
            if not force and time.monotonic() < self.retry_at.get(chunk["id"], 0):
                continue
            try:
                result = self.ai.transcribe(self.session / chunk["audio"], chunk["duration"],
                                            self.language)
                write_json(result_path, {**result, "chunk": chunk, "transcribed_at": now()})
                (self.session / "errors" / path.name).unlink(missing_ok=True)
                changed = True
            except Exception as exc:
                failed += 1
                count = self.failures.get(chunk["id"], 0) + 1
                self.failures[chunk["id"]] = count
                self.retry_at[chunk["id"]] = time.monotonic() + min(300, 10 * 2 ** min(count, 5))
                # SDK exception text can contain request URLs or credentials. Store only type.
                write_json(self.session / "errors" / path.name,
                           {"chunk_id": chunk["id"], "error_type": type(exc).__name__,
                            "http_status": getattr(exc, "code", None),
                            "at": now(), "retryable": True})
                log.warning("Transcription failed for %s (%s, HTTP %s); audio retained.",
                            chunk["id"], type(exc).__name__, getattr(exc, "code", "n/a"))
        if changed:
            export_transcript(self.session)
        return failed

    def finish(self) -> bool:
        transcript = export_transcript(self.session)
        chunks = list((self.session / "chunks").glob("*.json"))
        pending = len(chunks) - len(transcript["completed_chunks"])
        if pending:
            write_json(self.session / "processing.json", {"status": "pending", "pending": pending})
            return False
        try:
            if transcript["segments"]:
                export_notes(self.session, self.ai.summarize(transcript))
            else:
                export_notes(self.session, {"title": "No speech detected", "summary": "",
                                           "key_points": [], "decisions": [], "action_items": [],
                                           "open_questions": []})
            write_json(self.session / "processing.json", {"status": "complete", "pending": 0})
            return True
        except Exception as exc:
            write_json(self.session / "processing.json",
                       {"status": "notes_failed", "error_type": type(exc).__name__,
                        "http_status": getattr(exc, "code", None), "pending": 0})
            log.warning("Meeting notes failed (%s); transcript retained.", type(exc).__name__)
            return False


def recover_chunks(session: Path) -> None:
    """Repair chunks left open after a crash, using their periodically updated WAV headers."""
    for path in (session / "chunks").glob("*.json"):
        chunk = read_json(path)
        if chunk["ready"]:
            continue
        try:
            with wave.open(str(session / chunk["audio"]), "rb") as audio:
                chunk.update(duration=audio.getnframes() / audio.getframerate(), ready=True,
                             recovered=True)
            write_json(path, chunk)
        except (OSError, EOFError, wave.Error) as exc:
            write_json(session / "errors" / path.name,
                       {"chunk_id": chunk["id"], "error_type": type(exc).__name__,
                        "stage": "audio_recovery", "retryable": False})
            log.warning("Cannot recover %s; other chunks will still be processed.", chunk["id"])
