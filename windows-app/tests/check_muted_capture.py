"""Hardware regression: record a local tone at master volume zero and while muted.

Restores the original endpoint volume/mute in a finally block. Never contacts AI.
"""

import argparse
import array
import math
import time
import wave
import winsound
from pathlib import Path

from pycaw.pycaw import AudioUtilities

from ai_note_taker.audio import Capture
from ai_note_taker.runtime import create_session
from ai_note_taker.storage import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("recordings/volume-check"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = args.output / "tone.wav"
    samples = array.array("h", (int(9000 * math.sin(2 * math.pi * 440 * n / 48000))
                                for n in range(48000)))
    with wave.open(str(fixture), "wb") as stream:
        stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())
    endpoint = AudioUtilities.GetSpeakers().EndpointVolume
    original_volume = endpoint.GetMasterVolumeLevelScalar()
    original_mute = endpoint.GetMute()
    results = []
    try:
        for label, volume, muted in (("volume_zero", 0.0, False), ("muted", original_volume, True)):
            endpoint.SetMasterVolumeLevelScalar(volume, None)
            endpoint.SetMute(muted, None)
            session = create_session(args.output)
            capture = Capture(session, 5, False)
            try:
                capture.start()
                time.sleep(0.15)
                winsound.PlaySound(str(fixture), winsound.SND_FILENAME)
                time.sleep(0.15)
                capture.check()
            finally:
                capture.close()
            peak = 0
            frames = 0
            for path in (session / "audio").glob("*.wav"):
                with wave.open(str(path), "rb") as stream:
                    pcm = array.array("h", stream.readframes(stream.getnframes()))
                    frames += stream.getnframes()
                    peak = max(peak, max((abs(value) for value in pcm), default=0))
            result = {"case": label, "peak": peak, "frames": frames,
                      "passed": peak > 100, "session": str(session)}
            print(result, flush=True)
            results.append(result)
    finally:
        endpoint.SetMasterVolumeLevelScalar(original_volume, None)
        endpoint.SetMute(original_mute, None)
    write_json(args.output / "results.json", results)
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
