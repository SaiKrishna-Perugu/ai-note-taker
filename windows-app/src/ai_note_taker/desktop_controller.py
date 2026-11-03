"""GUI-independent recorder controls. All audio/AI work runs in a hidden worker."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from ai_note_taker.runtime import create_session, current, is_alive
from ai_note_taker.storage import read_json, write_json


def send_to_recycle_bin(path: Path) -> None:
    """Move a folder to the Windows Recycle Bin so a mistaken delete can be undone."""
    if os.name != "nt":
        shutil.rmtree(path)
        return
    import ctypes
    from ctypes import wintypes

    class FileOperation(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("function", wintypes.UINT), ("source", wintypes.LPCWSTR),
                    ("target", wintypes.LPCWSTR), ("flags", ctypes.c_ushort), ("aborted", wintypes.BOOL),
                    ("mappings", ctypes.c_void_p), ("title", wintypes.LPCWSTR)]

    delete, silent, no_confirm, allow_undo, no_error_ui = 3, 0x4, 0x10, 0x40, 0x400
    operation = FileOperation(None, delete, str(path) + "\0", None, silent | no_confirm | allow_undo | no_error_ui,
                              False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result or operation.aborted or path.exists():
        raise OSError(f"Windows could not move the recording to the Recycle Bin (code {result}).")


def worker_command(*args: str) -> list[str]:
    """Command line that runs this app's CLI in a separate hidden process."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", *args]
    return [sys.executable, "-m", "ai_note_taker", *args]


def list_devices(timeout: float = 10.0) -> list[dict]:
    """Enumerate audio devices in a child process.

    If Windows Audio is slow or stuck, the child blocks instead of the GUI; on timeout it is
    killed and an error is raised so the window stays responsive.
    """
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(worker_command("devices"), capture_output=True, text=True,
                                encoding="utf-8", timeout=timeout, stdin=subprocess.DEVNULL,
                                creationflags=flags)
    except subprocess.TimeoutExpired:
        raise TimeoutError("Windows Audio did not answer") from None
    if result.returncode != 0:
        raise RuntimeError("Audio device listing failed")
    return json.loads(result.stdout)


class DesktopController:
    def __init__(self, output: Path) -> None:
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.session: Path | None = None
        self.process: subprocess.Popen | None = None
        self.transcriber: subprocess.Popen | None = None
        self.transcribing_session: Path | None = None

    @staticmethod
    def _environment(env_file: Path | None) -> dict:
        environment = os.environ.copy()
        if env_file and Path(env_file).is_file():
            # A chosen file is authoritative for the worker, without mutating the GUI environment.
            # Blank lines (the template's empty GEMINI_API_KEY=) must not hide a real value.
            environment.update({key: value for key, value in dotenv_values(env_file).items() if value})
        return environment

    def effective_session(self) -> Path | None:
        if self.session is None:
            active = current(self.output)
            if active:
                self.session = active[0]
        return self.session

    def summary(self, session: Path | None = None) -> dict:
        """What is missing from a saved session: audio chunks without a transcript, and failures."""
        folder = session or self.effective_session()
        if folder is None or not folder.is_dir():
            return {"chunks": 0, "transcribed": 0, "pending": 0, "errors": 0, "record_only": False}
        chunks = len(list((folder / "chunks").glob("*.json")))
        transcribed = len(list((folder / "responses").glob("*.json")))
        errors = sorted((folder / "errors").glob("*.json"))
        first = {}
        if errors:
            try:
                first = read_json(errors[0])
            except (OSError, ValueError):
                first = {}
        try:
            record_only = bool(read_json(folder / "config.json").get("record_only"))
        except (OSError, ValueError):
            record_only = False
        try:
            processing = read_json(folder / "processing.json").get("status")
        except (OSError, ValueError):
            processing = None
        return {"chunks": chunks, "transcribed": transcribed, "pending": max(0, chunks - transcribed),
                "errors": len(errors), "error_type": first.get("error_type"),
                "http_status": first.get("http_status"), "record_only": record_only,
                "processing": processing}

    def transcribe(self, session: Path, env_file: Path | None) -> None:
        """Transcribe a saved session in a hidden process (same as the CLI `transcribe`)."""
        if self.transcribing():
            raise RuntimeError("A transcription is already running.")
        command = worker_command("--output", str(self.output),
                                 "--env-file", str(env_file or session / "unused.env"),
                                 "transcribe", str(session))
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with (session / "recorder.log").open("ab") as log:
            self.transcriber = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                creationflags=flags, env=self._environment(env_file))
        self.transcribing_session = session

    def transcribing(self) -> bool:
        return self.transcriber is not None and self.transcriber.poll() is None

    def transcribe_returncode(self) -> int | None:
        return None if self.transcriber is None else self.transcriber.returncode

    def snapshot(self) -> dict:
        if self.session is None:
            active = current(self.output)
            if active:
                self.session = active[0]
        if self.session is None:
            return {"status": "idle"}
        path = self.session / "session.json"
        if not path.exists():
            if self.process and self.process.poll() is not None:
                return {"status": "failed", "error": "Recorder could not start. See recorder.log."}
            return {"status": "starting"}
        state = read_json(path)
        state["alive"] = is_alive(state)
        if not state["alive"] and state["status"] in ("starting", "recording", "paused", "processing"):
            state["status"] = "interrupted"
        if (self.session / "processing.json").exists():
            state["processing"] = read_json(self.session / "processing.json")
        return state

    def start(self, config: dict, env_file: Path | None) -> Path:
        state = self.snapshot()
        if state.get("alive") or state["status"] == "starting":
            raise RuntimeError("A recording is already active.")
        active = current(self.output)
        if active and is_alive(active[1]):
            raise RuntimeError("Another recorder is using this recordings folder.")
        session = create_session(self.output)
        write_json(session / "config.json", config)
        command = worker_command("--output", str(self.output),
                                 "--env-file", str(env_file or session / "unused.env"),
                                 "_worker", str(session))
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with (session / "recorder.log").open("ab") as log:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            creationflags=flags, start_new_session=os.name != "nt",
                                            env=self._environment(env_file))
        self.session = session
        return session

    def pause(self) -> None:
        if self.session and self.snapshot()["status"] == "recording":
            (self.session / "pause.request").touch()

    def resume(self) -> None:
        if self.session and self.snapshot()["status"] == "paused":
            (self.session / "pause.request").unlink(missing_ok=True)

    def stop(self) -> None:
        if self.session and self.snapshot().get("alive"):
            (self.session / "stop.request").touch()

    def describe(self, session: Path) -> dict:
        """Facts shown in the delete confirmation: when, how long, and what is saved."""
        try:
            seconds = float(read_json(session / "session.json").get("active_seconds", 0))
        except (OSError, ValueError, TypeError):
            seconds = 0.0
        return {"name": session.name, "seconds": seconds,
                "has_transcript": (session / "transcript.txt").is_file() and (session / "transcript.txt").stat().st_size > 0,
                "has_notes": (session / "notes.md").is_file()}

    def can_delete(self, session: Path) -> bool:
        """Only real sessions inside the recordings folder, and never one that is still in use."""
        try:
            inside = session.resolve().parent == self.output
        except OSError:
            return False
        if not inside or not (session / "session.json").is_file():
            return False
        try:
            state = read_json(session / "session.json")
        except (OSError, ValueError):
            return True                                  # unreadable state: nothing live to protect
        busy = is_alive(state) and state.get("status") in ("starting", "recording", "paused", "processing")
        return not busy and not (self.transcribing() and self.transcribing_session == session)

    def delete_session(self, session: Path) -> None:
        """Move one recording (audio, transcript, notes) to the Recycle Bin."""
        if not self.can_delete(session):
            raise RuntimeError("This recording is in use or is not one of your saved recordings.")
        send_to_recycle_bin(session.resolve())
        if self.session is not None and self.session.resolve() == session.resolve():
            self.session = None
        remaining = self.sessions()
        pointer = self.output / "current.json"
        if remaining:
            write_json(pointer, {"session_id": remaining[0].parent.name})   # newest first
        else:
            pointer.unlink(missing_ok=True)

    def sessions(self) -> list[Path]:
        return sorted(self.output.glob("*/session.json"), reverse=True)

    def transcript(self, session: Path | None = None) -> str:
        folder = session or self.session
        path = folder / "transcript.txt" if folder else None
        return path.read_text(encoding="utf-8") if path and path.exists() else ""

    def notes(self, session: Path | None = None) -> str:
        folder = session or self.session
        path = folder / "notes.md" if folder else None
        return path.read_text(encoding="utf-8") if path and path.exists() else ""
