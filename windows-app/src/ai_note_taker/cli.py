"""Command line entry point; starting a recorder is always explicit."""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from ai_note_taker.ai import DEFAULT_MODEL, Gemini, validate_configuration
from ai_note_taker.audio import devices
from ai_note_taker.processing import Processor, recover_chunks
from ai_note_taker.runtime import create_session, current, is_alive, recorder_lock, run_session
from ai_note_taker.storage import read_json, write_json


def positive(value: str) -> float:
    import math

    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Must be a positive finite number")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Record Windows meeting audio and save AI transcripts.")
    result.add_argument("--output", type=Path, default=Path("recordings"),
                        help="Session storage folder (default: ./recordings)")
    result.add_argument("--env-file", type=Path, default=Path(".env"),
                        help="Optional dotenv file; environment variables take precedence")
    commands = result.add_subparsers(dest="command")
    commands.add_parser("devices", help="List loopback and microphone input devices")
    for name, help_text in (("start", "Start recording in the background"),
                            ("record", "Record in this terminal; Ctrl+C stops")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--microphone", action="store_true", help="Also capture your microphone")
        command.add_argument("--system-device", type=int, help="Loopback index from devices")
        command.add_argument("--mic-device", type=int, help="Microphone index (requires --microphone)")
        command.add_argument("--chunk-seconds", type=positive, default=30,
                             help="Transcribe completed chunks of this length (default 30; max 60)")
        command.add_argument("--duration", type=positive, default=None,
                             help="Automatically stop after this many seconds")
        command.add_argument("--record-only", action="store_true", help="Keep audio for later transcription")
        command.add_argument("--model", default=None)
        command.add_argument("--language", default="auto", help="Spoken language hint; default auto")
    commands.add_parser("status", help="Show recorder state and pending chunk count")
    commands.add_parser("pause", help="Pause audio recording")
    commands.add_parser("resume", help="Resume audio recording")
    stop = commands.add_parser("stop", help="Stop capture and let pending transcription finish")
    stop.add_argument("--wait", type=positive, default=15, help="Wait up to this many seconds")
    transcribe = commands.add_parser("transcribe", help="Retry saved audio and regenerate meeting notes")
    transcribe.add_argument("session", type=Path)
    transcribe.add_argument("--model", default=None)
    commands.add_parser("sessions", help="List saved recording sessions")
    worker = commands.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("session", type=Path)
    return result


def report(root: Path) -> dict:
    active = current(root)
    if active is None:
        return {"status": "idle"}
    session, state = active
    alive = is_alive(state)
    status = state["status"]
    if not alive and status in ("starting", "recording", "paused", "processing"):
        status = "interrupted"
    chunks = list((session / "chunks").glob("*.json"))
    responses = list((session / "responses").glob("*.json"))
    return {"status": status, "process_alive": alive, "session": str(session),
            "chunks": len(chunks), "transcribed_chunks": len(responses),
            "pending_chunks": len(chunks) - len(responses),
            "processing": read_json(session / "processing.json")
            if (session / "processing.json").exists() else None,
            **{key: state[key] for key in ("sources", "error", "error_type") if key in state}}


def execute(args: argparse.Namespace) -> int:
    root = args.output.resolve()
    if args.command == "devices":
        print(json.dumps(devices(), indent=2, ensure_ascii=False))
    elif args.command in ("start", "record"):
        if args.chunk_seconds > 60:
            raise ValueError("Use --chunk-seconds of 60 or less to keep AI requests small.")
        if args.mic_device is not None and not args.microphone:
            raise ValueError("--mic-device requires --microphone")
        if not args.record_only:
            validate_configuration()
        active = current(root)
        if active and is_alive(active[1]):
            raise RuntimeError("A recorder is already running. Use status or stop.")
        config = {key: getattr(args, key) for key in
                  ("microphone", "system_device", "mic_device", "chunk_seconds", "duration",
                   "record_only", "model", "language")}
        session = create_session(root)
        write_json(session / "config.json", config)
        if args.command == "record":
            print(f"Recording session: {session}\nPress Ctrl+C to stop.", flush=True)
            run_session(root, session, config)
            print(json.dumps(report(root), indent=2))
            state = read_json(session / "session.json")
            return 1 if state["status"] == "failed" else 0
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        with (session / "recorder.log").open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "ai_note_taker", "--output", str(root),
                 "--env-file", str(args.env_file.resolve()), "_worker", str(session)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags,
                start_new_session=os.name != "nt", cwd=str(Path.cwd()))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state_path = session / "session.json"
            if state_path.exists():
                state = read_json(state_path)
                if state["status"] == "failed":
                    raise RuntimeError(f"Recorder failed: {state.get('error', state.get('error_type'))}. "
                                       f"See {session / 'recorder.log'}")
                if state["status"] != "starting":
                    print(f"Background recording started. Session: {session}")
                    return 0
            if process.poll() is not None:
                raise RuntimeError(f"Recorder exited. See {session / 'recorder.log'}")
            time.sleep(0.1)
        raise RuntimeError(f"Recorder is still starting. Check status and {session / 'recorder.log'}")
    elif args.command == "_worker":
        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
        run_session(root, args.session, read_json(args.session / "config.json"))
    elif args.command == "status":
        print(json.dumps(report(root), indent=2, ensure_ascii=False))
    elif args.command in ("pause", "resume"):
        active = current(root)
        if active is None or not is_alive(active[1]):
            raise RuntimeError("No recorder is running.")
        path = active[0] / "pause.request"
        if args.command == "pause":
            path.touch()
        else:
            path.unlink(missing_ok=True)
        print(f"{args.command.title()} requested.")
    elif args.command == "stop":
        active = current(root)
        if active is None or not is_alive(active[1]):
            print("No recorder is running.")
            return 0
        session, state = active
        (session / "stop.request").touch()
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline and is_alive(state):
            time.sleep(0.2)
        print(json.dumps(report(root), indent=2, ensure_ascii=False))
        if is_alive(state):
            print("Stop requested; pending transcription is still finishing. Use status to check.")
    elif args.command == "transcribe":
        session = args.session.resolve()
        state = read_json(session / "session.json")
        if is_alive(state):
            raise RuntimeError("Stop the recording and wait for processing before retrying it.")
        with recorder_lock(session.parent):
            config = read_json(session / "config.json")
            ai = Gemini(args.model or config["model"])
            try:
                recover_chunks(session)
                processor = Processor(session, ai, config["language"])
                processor.pass_once(force=True)
                success = processor.finish()
                print(f"{'Complete' if success else 'Pending; see errors/ and processing.json'}: {session}")
                return 0 if success else 1
            finally:
                ai.close()
    elif args.command == "sessions":
        for path in sorted(root.glob("*/session.json"), reverse=True):
            state = read_json(path)
            print(f"{path.parent.name}  {state['status']}  {state['started_at']}")
    return 0


def main() -> None:
    argument_parser = parser()
    args = argument_parser.parse_args()
    if args.command is None:
        argument_parser.print_help()
        return
    load_dotenv(args.env_file, override=False)
    if args.command in ("start", "record"):
        args.model = args.model or os.getenv("GEMINI_MODEL") or os.getenv("MODEL_ID", DEFAULT_MODEL)
    try:
        raise SystemExit(execute(args))
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
