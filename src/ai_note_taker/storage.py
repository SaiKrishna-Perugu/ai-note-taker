"""Atomic local storage and reproducible exports from per-chunk responses."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def export_transcript(session: Path) -> dict:
    """Rebuild exports; a failed/repeated request cannot duplicate utterances."""
    segments = []
    completed = []
    for result_path in sorted((session / "responses").glob("*.json")):
        result = read_json(result_path)
        chunk = result["chunk"]
        completed.append(chunk["id"])
        for segment in result["transcript"]["segments"]:
            segments.append({
                **segment,
                "start": round(chunk["start"] + segment["start"], 3),
                "end": round(chunk["start"] + segment["end"], 3),
                "source": chunk["source"],
                "chunk_id": chunk["id"],
                # AI speaker labels are local to each source/chunk.
                "speaker_id": f"{chunk['id']}:{segment['speaker']}",
            })
    segments.sort(key=lambda segment: (segment["start"], segment["source"]))
    result = {"schema_version": 1, "session_id": session.name,
              "completed_chunks": completed, "segments": segments}
    write_json(session / "transcript.json", result)
    write_text(session / "transcript.jsonl", "".join(
        json.dumps(segment, ensure_ascii=False) + "\n" for segment in segments))
    write_text(session / "transcript.txt", "".join(
        f"[{timestamp(s['start'])} - {timestamp(s['end'])}] "
        f"{s['source']} / {s['speaker']}: {s['text']}\n" for s in segments))
    return result


def export_notes(session: Path, notes: dict) -> None:
    write_json(session / "notes.json", notes)
    lines = [f"# {notes['title']}", "", notes["summary"], ""]
    for key, heading in (("key_points", "Key points"), ("decisions", "Decisions"),
                         ("open_questions", "Open questions")):
        lines.extend([f"## {heading}", ""])
        lines.extend(f"- {item}" for item in notes[key])
        lines.append("")
    lines.extend(["## Action items", ""])
    for action in notes["action_items"]:
        lines.append(f"- [ ] {action['task']} (owner: {action['owner'] or 'unspecified'}; "
                     f"due: {action['due_date'] or 'unspecified'})")
    write_text(session / "notes.md", "\n".join(lines) + "\n")
