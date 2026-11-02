"""Windows WASAPI loopback and optional microphone recording."""

import queue
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

from ai_note_taker.storage import write_json


def backend() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("System audio capture currently requires Windows (WASAPI).")
    import pyaudiowpatch

    return pyaudiowpatch


def devices() -> list[dict]:
    pa = backend()
    with pa.PyAudio() as manager:
        return [{"index": int(d["index"]), "name": d["name"],
                 "loopback": bool(d.get("isLoopbackDevice")),
                 "input_channels": int(d["maxInputChannels"]),
                 "sample_rate": int(d["defaultSampleRate"])}
                for d in manager.get_device_info_generator() if d["maxInputChannels"]]


class ChunkWriter:
    """Single writer per source; only closed WAVs are published as ready."""

    def __init__(self, session: Path, source: str, rate: int, channels: int,
                 chunk_seconds: float) -> None:
        self.session, self.source = session, source
        self.rate, self.channels = rate, channels
        self.limit = int(rate * chunk_seconds)
        self.sequence = 0
        self.frames = 0
        self.stream = None
        self.chunk: dict = {}

    def write(self, data: bytes, start: float) -> None:
        frame_size = self.channels * 2
        while data:
            if self.stream is None:
                chunk_id = f"{self.source}-{self.sequence:06}"
                self.chunk = {"id": chunk_id, "source": self.source, "start": max(0, start),
                              "rate": self.rate, "channels": self.channels, "ready": False,
                              "audio": f"audio/{chunk_id}.wav"}
                write_json(self.session / "chunks" / f"{chunk_id}.json", self.chunk)
                self.stream = wave.open(str(self.session / self.chunk["audio"]), "wb")
                self.stream.setparams((self.channels, 2, self.rate, 0, "NONE", "not compressed"))
                self.frames = 0
            take = min(len(data) // frame_size, self.limit - self.frames)
            self.stream.writeframes(data[:take * frame_size])
            self.frames += take
            data = data[take * frame_size:]
            start += take / self.rate
            if self.frames >= self.limit:
                self.close()

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
            self.chunk.update(duration=self.frames / self.rate, ready=True)
            write_json(self.session / "chunks" / f"{self.chunk['id']}.json", self.chunk)
            self.sequence += 1


class Capture:
    def __init__(self, session: Path, chunk_seconds: float, microphone: bool,
                 system_device: int | None = None, mic_device: int | None = None) -> None:
        self.session, self.chunk_seconds = session, chunk_seconds
        self.microphone, self.system_device, self.mic_device = microphone, system_device, mic_device
        self.streams: list = []
        self.threads: list[threading.Thread] = []
        self.queues: list[queue.Queue] = []
        self.errors: list[str] = []
        self.sources: list[dict] = []
        self.manager = None
        self.origin = time.monotonic()

    def _writer(self, packets: queue.Queue, writer: ChunkWriter) -> None:
        expected = None
        try:
            while (packet := packets.get()) is not None:
                data, start = packet
                # Preserve real elapsed time if the endpoint stops delivering packets.
                if expected is not None and start - expected > 0.5:
                    writer.close()
                writer.write(data, start if expected is None else max(start, expected))
                expected = start + len(data) / (writer.channels * 2 * writer.rate)
        except Exception as exc:
            self.errors.append(f"Audio file write failed: {type(exc).__name__}")
        finally:
            writer.close()

    def start(self) -> None:
        pa = backend()
        self.manager = pa.PyAudio()
        try:
            system = (self.manager.get_default_wasapi_loopback() if self.system_device is None
                      else self.manager.get_device_info_by_index(self.system_device))
            if not system.get("isLoopbackDevice"):
                raise ValueError("--system-device must identify a loopback device; run devices.")
            selected = [("system", system)]
            if self.microphone:
                mic = (self.manager.get_default_input_device_info() if self.mic_device is None
                       else self.manager.get_device_info_by_index(self.mic_device))
                if mic.get("isLoopbackDevice") or not mic["maxInputChannels"]:
                    raise ValueError("Select a microphone input using --mic-device.")
                selected.append(("microphone", mic))
            for source, device in selected:
                rate, channels = int(device["defaultSampleRate"]), int(device["maxInputChannels"])
                if channels * rate * 2 * self.chunk_seconds > 14_000_000:
                    raise ValueError("Chunk too large for this device; reduce --chunk-seconds.")
                packets: queue.Queue = queue.Queue(maxsize=256)
                writer = ChunkWriter(self.session, source, rate, channels, self.chunk_seconds)
                thread = threading.Thread(target=self._writer, args=(packets, writer), daemon=True)

                def callback(data, frame_count, time_info, status, *, packets=packets,
                             rate=rate):
                    if status:
                        self.errors.append(f"Audio capture reported dropped data (status {status}).")
                    try:
                        packets.put_nowait((data, time.monotonic() - self.origin - frame_count / rate))
                    except queue.Full:
                        self.errors.append("Audio writer fell behind; capture stopped to avoid silent loss.")
                        return (None, pa.paAbort)
                    return (None, pa.paContinue)

                stream = self.manager.open(
                    format=pa.paInt16, channels=channels, rate=rate, input=True,
                    input_device_index=int(device["index"]), frames_per_buffer=1024,
                    stream_callback=callback, start=False)
                self.streams.append(stream)
                self.queues.append(packets)
                self.threads.append(thread)
                self.sources.append({"source": source, "device": device["name"],
                                     "index": int(device["index"]), "rate": rate,
                                     "channels": channels})
                thread.start()
            self.origin = time.monotonic()
            for stream in self.streams:
                stream.start_stream()
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        if self.errors:
            raise RuntimeError(self.errors[0])
        if any(not stream.is_active() for stream in self.streams):
            raise RuntimeError("An audio device stopped. Check the output device and start a new session.")

    def close(self) -> None:
        for stream in self.streams:
            try:
                stream.stop_stream()
            finally:
                stream.close()
        self.streams.clear()
        for packets, thread in zip(self.queues, self.threads):
            while thread.is_alive():
                try:
                    packets.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue
            thread.join()
        self.queues.clear()
        self.threads.clear()
        if self.manager is not None:
            self.manager.terminate()
            self.manager = None
