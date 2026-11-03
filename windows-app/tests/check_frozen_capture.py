"""Exercise a built .exe worker: muted capture, pause exclusion, resume, stop, AI.

Uses only locally generated sample audio. Restores endpoint volume/mute on exit.
"""

import argparse
import array
import math
import sys
import time
import wave
import winsound
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values
from pycaw.pycaw import AudioUtilities

from ai_note_taker.desktop_controller import DesktopController
from ai_note_taker.storage import read_json, write_json


def wait_for(controller: DesktopController, states: set[str], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = controller.snapshot()
        if state["status"] in states:
            return state
        if state["status"] in ("failed", "interrupted"):
            raise RuntimeError(f"Worker failed: {state.get('error_type', state['status'])}")
        time.sleep(.1)
    raise TimeoutError(f"Worker did not reach {states}")


def energy(pcm: list[int], frequency: int, rate: float) -> float:
    real = sum(sample * math.cos(2 * math.pi * frequency * n / rate) for n, sample in enumerate(pcm))
    imaginary = sum(sample * math.sin(2 * math.pi * frequency * n / rate) for n, sample in enumerate(pcm))
    return real * real + imaginary * imaginary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, help="Omit to check the source application's worker")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--speech", type=Path)
    parser.add_argument("--with-ai", action="store_true")
    args = parser.parse_args()
    root = Path("recordings/frozen-check").resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixtures = []
    for frequency in (440, 880, 660):
        path = root / f"tone-{frequency}.wav"
        samples = array.array("h", (int(10000 * math.sin(2 * math.pi * frequency * n / 48000))
                                    for n in range(24000)))
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            audio.writeframes(samples.tobytes())
        fixtures.append(path)
    values = dotenv_values(args.env_file) if args.env_file else {}
    controller = DesktopController(root)
    config = {"record_only": not args.with_ai, "microphone": False, "system_device": None,
              "mic_device": None, "duration": 45, "chunk_seconds": 20, "language": "auto",
              "model": values.get("GEMINI_MODEL") or values.get("MODEL_ID", "gemini-2.5-flash")}
    endpoint = AudioUtilities.GetSpeakers().EndpointVolume
    original_volume, original_mute = endpoint.GetMasterVolumeLevelScalar(), endpoint.GetMute()
    try:
        endpoint.SetMasterVolumeLevelScalar(0, None)
        endpoint.SetMute(True, None)
        if args.executable:
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(args.executable.resolve())):
                session = controller.start(config, args.env_file.resolve() if args.env_file else None)
        else:
            session = controller.start(config, args.env_file.resolve() if args.env_file else None)
        wait_for(controller, {"recording"})
        print("Desktop worker recording at Windows volume zero + mute", flush=True)
        winsound.PlaySound(str(fixtures[0]), winsound.SND_FILENAME)
        if args.speech:
            winsound.PlaySound(str(args.speech.resolve()), winsound.SND_FILENAME)
        controller.pause()
        wait_for(controller, {"paused"})
        winsound.PlaySound(str(fixtures[1]), winsound.SND_FILENAME)
        controller.resume()
        wait_for(controller, {"recording"})
        winsound.PlaySound(str(fixtures[2]), winsound.SND_FILENAME)
        time.sleep(.15)
        controller.stop()
    finally:
        endpoint.SetMasterVolumeLevelScalar(original_volume, None)
        endpoint.SetMute(original_mute, None)
        controller.stop()
    state = wait_for(controller, {"stopped"}, timeout=240)
    chunks = sorted((session / "audio").glob("*.wav"))
    included = {440: 0.0, 660: 0.0}
    excluded = 0.0
    frames = 0
    for path in chunks:
        with wave.open(str(path), "rb") as audio:
            frames += audio.getnframes()
            pcm = array.array("h", audio.readframes(audio.getnframes()))
            # First channel, decimated to keep the spectral check inexpensive.
            samples = list(pcm[::audio.getnchannels() * 12])
            rate = audio.getframerate() / 12
        for frequency in included:
            included[frequency] = max(included[frequency], energy(samples, frequency, rate))
        excluded = max(excluded, energy(samples, 880, rate))
    assert included[440] > 1e10 and included[660] > 1e10, "Expected tones were not captured."
    assert excluded < min(included.values()) * .02, "Audio from the paused interval was recorded."
    assert state["pause_intervals"][0]["end"] is not None
    result = {"passed": True, "session": str(session), "chunks": len(chunks), "frames": frames,
              "executable": str(args.executable) if args.executable else "source",
              "windows_volume_zero": True, "windows_muted": True,
              "pause_audio_excluded": True, "included_tone_energy": included,
              "excluded_tone_energy": excluded}
    if args.with_ai:
        assert read_json(session / "processing.json")["status"] == "complete", "AI processing incomplete."
        assert "proposal" in (session / "transcript.txt").read_text(encoding="utf-8").lower()
        assert read_json(session / "notes.json")["summary"]
        result["ai_transcript_and_notes"] = True
    write_json(root / "results.json", result)
    print(result)


if __name__ == "__main__":
    main()
