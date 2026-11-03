"""GUI-independent recorder controls. All audio/AI work runs in a hidden worker."""

import json
import os
import subprocess
import sys
from pathlib import Path

from ai_note_taker.runtime import create_session, current, is_alive
from ai_note_taker.storage import read_json, write_json


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
        environment = os.environ.copy()
        if env_file:
            from dotenv import dotenv_values

            # A chosen file is authoritative for this worker, without mutating GUI environment.
            environment.update({key: value for key, value in dotenv_values(env_file).items() if value is not None})
        with (session / "recorder.log").open("ab") as log:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                            creationflags=flags, start_new_session=os.name != "nt",
                                            env=environment)
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
