"""Recorder lifecycle and a process lock shared by foreground/background commands."""

import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psutil

from ai_note_taker.ai import Gemini
from ai_note_taker.audio import Capture
from ai_note_taker.processing import Processor
from ai_note_taker.storage import export_transcript, now, read_json, write_json


@contextmanager
def recorder_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "recorder.lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("A recorder or transcription process already owns this output folder.") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def is_alive(state: dict) -> bool:
    try:
        process = psutil.Process(state["pid"])
        return process.is_running() and abs(process.create_time() - state["process_created"]) < 0.01
    except (KeyError, psutil.Error):
        return False


def current(root: Path) -> tuple[Path, dict] | None:
    pointer = root / "current.json"
    if not pointer.exists():
        return None
    try:
        session = root / read_json(pointer)["session_id"]
        return session, read_json(session / "session.json")
    except (OSError, ValueError, KeyError):
        return None          # pointer left behind by a deleted or damaged session


def run_session(root: Path, session: Path, config: dict) -> None:
    state = {"schema_version": 1, "session_id": session.name, "status": "starting",
             "started_at": now(), "pid": os.getpid(),
             "process_created": psutil.Process().create_time(), "config": config}
    capture = None
    ai = None
    done = threading.Event()
    stop = threading.Event()
    processor_thread = None

    def request_stop(signum, frame):
        stop.set()

    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous_handlers:
        signal.signal(sig, request_stop)

    try:
        with recorder_lock(root):
            write_json(session / "session.json", state)
            write_json(root / "current.json", {"session_id": session.name})
            try:
                if not config["record_only"]:
                    ai = Gemini(config["model"])
                    processor = Processor(session, ai, config["language"])

                    def process() -> None:
                        try:
                            while not done.wait(0.5):
                                processor.pass_once()
                            processor.pass_once(force=True)
                            processor.finish()
                        except Exception as exc:
                            write_json(session / "processing.json",
                                       {"status": "failed", "error_type": type(exc).__name__})

                    processor_thread = threading.Thread(target=process, daemon=True)
                capture = Capture(session, config["chunk_seconds"], config["microphone"],
                                  config["system_device"], config["mic_device"])
                capture.start()
                state.update(status="recording", sources=capture.sources)
                write_json(session / "session.json", state)
                if processor_thread:
                    processor_thread.start()
                origin = time.monotonic()
                heartbeat = origin
                pause_started = None
                paused_seconds = 0.0
                state["pause_intervals"] = []
                while not stop.wait(0.2):
                    capture.check()
                    if (session / "stop.request").exists():
                        break
                    if config["duration"] and time.monotonic() - origin >= config["duration"]:
                        break
                    wants_pause = (session / "pause.request").exists()
                    if wants_pause and state["status"] == "recording":
                        capture.pause()
                        pause_started = time.monotonic()
                        state["pause_intervals"].append({"start": pause_started - origin, "end": None})
                        state["status"] = "paused"
                        write_json(session / "session.json", state)
                    elif not wants_pause and state["status"] == "paused":
                        paused_seconds += time.monotonic() - pause_started
                        state["pause_intervals"][-1]["end"] = time.monotonic() - origin
                        pause_started = None
                        capture.resume()
                        state["status"] = "recording"
                        write_json(session / "session.json", state)
                    if time.monotonic() - heartbeat >= 0.5:
                        paused_now = time.monotonic() - pause_started if pause_started is not None else 0
                        state["active_seconds"] = time.monotonic() - origin - paused_seconds - paused_now
                        state["elapsed_seconds"] = time.monotonic() - origin
                        state["levels"] = dict(capture.levels)
                        state["heartbeat_at"] = now()
                        write_json(session / "session.json", state)
                        heartbeat = time.monotonic()
            except Exception as exc:
                state.update(status="failed", error_type=type(exc).__name__)
                # Capture/configuration messages are local, SDK errors are recorded only by type.
                if isinstance(exc, (ValueError, RuntimeError, OSError)) and ai is None:
                    state["error"] = str(exc)
                logging.error("Session failed (%s)", type(exc).__name__)
            finally:
                if capture:
                    try:
                        capture.close()
                        if capture.errors:
                            state.update(status="failed", error=capture.errors[0])
                    except Exception as exc:
                        state.update(status="failed", error_type=type(exc).__name__)
                state["recording_stopped_at"] = now()
                if state.get("pause_intervals") and state["pause_intervals"][-1]["end"] is None:
                    state["pause_intervals"][-1]["end"] = time.monotonic() - origin
                if state["status"] != "failed":
                    state["status"] = "processing"
                write_json(session / "session.json", state)
                done.set()
                if processor_thread and processor_thread.ident is not None:
                    processor_thread.join()
                export_transcript(session)
                if state["status"] != "failed":
                    state["status"] = "stopped"
                if config["record_only"]:
                    write_json(session / "processing.json", {"status": "not_requested"})
                state["finished_at"] = now()
                write_json(session / "session.json", state)
    except RuntimeError as exc:
        state.update(status="failed", error=str(exc), finished_at=now())
        write_json(session / "session.json", state)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        if ai:
            ai.close()


def create_session(root: Path) -> Path:
    from datetime import datetime
    from uuid import uuid4

    session = root / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    for directory in ("audio", "chunks", "responses", "errors"):
        (session / directory).mkdir(parents=True, exist_ok=True)
    return session
