# AI Note Taker Desktop

This is a separate desktop copy of the parent project. It adds a native Windows
interface, pause/resume, a system tray menu, and a draggable mini recorder.

## Launch the Windows app

On this machine, double-click **AI Note Taker.lnk** in this folder. It opens the
app without a console using this copy's `.venv` and installed Python. The shortcut
can be recreated with `./create_launcher.ps1` after `uv sync`.

The portable build is `dist/AI Note Taker/AI Note Taker.exe`. Keep its `_internal`
folder alongside the executable. **Windows Application Control blocked this
unsigned build on this PC with error 4551.** A trusted signing or IT approval
path is required to run that build here. No security settings were changed.
The installed-Python launcher runs the source application; it requires Python
and this copy's virtual environment.

## Desktop controls

- **Record:** begin system-audio capture and Gemini transcription.
- **Pause / Resume:** discard audio while paused, then continue in the same session.
  Previously captured chunks can still finish transcribing during a pause.
- **Stop:** save the final audio chunk, finish transcription, and generate notes.
- **Minimize / Mini recorder:** keep the main window minimized in the Windows
  taskbar and show a small, circular recorder bubble. Drag the circle to reposition
  it. Click it for recording controls; **Collapse to circle** makes it compact again.
- **− on the bubble or controls:** minimize the popup too, while recording keeps
  running. Restore the main window from the taskbar or tray whenever needed.
- **× on the recorder controls:** dismiss the popup while recording continues.
  Restoring the main window also hides the floating bubble automatically.
- **Close:** keep the mini recorder available. Use **Quit** from the tray menu to
  exit, or use **Quit app** on the mini recorder; an active worker stops capture
  and finishes saving in the background.

The main window shows saved sessions, the original transcript, meeting notes,
audio-device choices, an optional microphone, and a save-audio-only mode.
Choose your existing `.env` with **Choose .env file**. No credentials are embedded
in the executable or copied into this project. A nearby parent `.env` is discovered
automatically during local development. The selected file configures each worker.

By default, the desktop app writes to `Documents/AI Note Taker/Recordings`.
**Change recordings folder** selects a different location. Transcripts update
after completed 20-second audio chunks plus API latency. Pause intervals are saved
in `session.json`; transcript timestamps include elapsed pauses, while the displayed
recording timer excludes them.

## Recording at volume zero

Verified on this PC with a known test tone: capture succeeded at Windows master
volume **0**, with Windows **muted**, and with both applied together. Playback
volume is independent of the captured audio on this audio endpoint. Other device
drivers can behave differently. The meeting/browser/player must keep rendering
audio; muting or stopping the source itself can produce silence.

## Build and validate the desktop copy

```powershell
uv sync --group dev
uv run ai-note-taker-desktop
uv run python -m unittest discover -s tests -v
./build_windows.ps1
```

The build script produces the portable Windows application. A signing certificate
or an IT-approved distribution route is needed where Application Control rejects
unsigned executables. To validate hardware without AI, run
`uv run python tests/check_muted_capture.py`. It temporarily sets zero/mute and
restores both original settings in `finally`. Automated tests use mocked audio/AI
and also verify pause exclusion and mini recorder controls.

The CLI and storage pipeline from the original project are retained below.

## CLI application

A Windows background recorder for calls and meetings. Captures the audio playing
through your selected speakers/headphones using WASAPI loopback, transcribes
completed audio chunks with Gemini, and saves the original transcript alongside
structured meeting notes. Add microphone capture to include your own voice.

## Setup

Requires Windows, Python 3.13+, and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
Copy-Item .env.example .env  # Only if you do not already have .env
```

Set `GEMINI_API_KEY` in `.env` or in your environment. Environment variables take
precedence. `GEMINI_MODEL` selects the model (`MODEL_ID` is also accepted);
`--model` overrides it. Recordings
and `.env` are ignored by Git. Existing prototype scripts are separate from the
application; `gemini.py` still needs its own LangChain dependencies.

`ALM_LLM_PROVIDER` defaults to `aistudio`. Existing Vertex configurations are also
supported: `vertex_express` uses an Express API key; `vertex_adc` uses Application
Default Credentials with `PROJECT_ID` and optional `LOCATION` (default `global`).
ADC mode does not use `GEMINI_API_KEY`. Configure ADC with your Google Cloud tools
before starting the recorder. See the [Google SDK authentication guide](https://googleapis.github.io/python-genai/).

## Record a meeting

```powershell
uv run ai-note-taker devices
uv run ai-note-taker start --microphone
uv run ai-note-taker status
uv run ai-note-taker stop
```

`start` launches a hidden background process that survives closing the terminal.
Recording begins only when you run `start` or `record`; there is no automatic
meeting detection or Windows startup registration. Without `--microphone`, only
system audio is recorded. Headphones help prevent microphone echo/duplicate
speech. The default microphone can be a virtual input: use the index shown by
`devices` to select the physical microphone if necessary.

```powershell
# Foreground recording; Ctrl+C stops and flushes the final audio chunk.
uv run ai-note-taker record --microphone --mic-device 15
# Bounded recording, with transcripts updated about every 30s plus API latency.
uv run ai-note-taker start --duration 1800 --chunk-seconds 30
# Capture without a key/network; transcribe the saved session later.
uv run ai-note-taker start --record-only
uv run ai-note-taker transcribe recordings/SESSION_ID
uv run ai-note-taker sessions
```

Use `--system-device INDEX` to choose a **loopback** device. Indices are machine
specific and can change. Keep the selected output device connected throughout
the session; device changes require stopping and starting a new recording.
All audio playing on that output is included, not just the meeting application.

Global options go before the command:

```powershell
uv run ai-note-taker --output "D:/Meetings" --env-file .env start --microphone
uv run ai-note-taker --output "D:/Meetings" stop --wait 30
```

Use the same output folder for start/status/stop. One recorder or retry process
may own an output folder at a time. `stop` signals capture to finish; if API calls
are still running after the wait period, it reports processing in progress.

## Saved output

Each session gets a unique folder under `recordings/`:

| File | Contents |
| --- | --- |
| `audio/*.wav` | Original PCM audio chunks, separately for system/microphone |
| `transcript.txt` | Verbatim transcript with elapsed timestamps and source |
| `transcript.json` | Timestamped segments, source, speaker, chunk IDs |
| `transcript.jsonl` | One structured segment per line |
| `responses/*.json` | Original AI transcription response for each chunk |
| `notes.md`, `notes.json` | Summary, key points, decisions, action items, questions |
| `session.json`, `processing.json` | Capture state and processing outcome |
| `errors/*.json`, `recorder.log` | Failed chunk identifiers and background diagnostics |

Transcript exports update as completed chunks are processed. Notes are generated
after capture stops and every chunk succeeds. API failures leave raw audio intact
and are retried with backoff while recording. Run `transcribe` after the recorder
has stopped to retry pending chunks or failed notes. Successful chunks are reused,
so retries do not duplicate text. Interrupted WAVs with valid headers are recovered
by this command. Disk failure or abrupt power loss can still damage the active chunk.

Example segment (times are seconds from the start of recording):

```json
{"start": 30.4, "end": 32.1, "source": "system", "speaker": "Speaker 1",
 "speaker_id": "system-000001:Speaker 1", "chunk_id": "system-000001",
 "text": "Let's send the proposal on Friday."}
```

Speaker labels are estimates local to each chunk; identities are not reconciled
across chunks. Chunk boundaries can split words. Original language is preserved;
`--language English` (or another language) provides a hint. Gemini may make errors;
review transcripts and notes against the saved audio when accuracy matters.
Audio chunks are sent to Google's Gemini API, and transcripts are sent for notes;
API usage can incur charges. Record meetings with participants' knowledge.

## Development

Reusable code lives in `src/ai_note_taker/`: `audio.py` captures audio, `ai.py`
calls Gemini, `processing.py` handles retries, `storage.py` writes exports,
and `runtime.py` / `cli.py` manage recording controls.

```powershell
uv run python -m unittest discover -s tests -v
uv run ai-note-taker
uv build
```

Tests use standard-library `unittest` with mocked Gemini requests and synthetic
PCM audio; routine tests need neither credentials nor audio hardware. Hardware
validation requires a Windows loopback recording while speech is playing on the
selected output, then verifying `transcript.txt` and `notes.json`.

`powershell -ExecutionPolicy Bypass -File tests/smoke_windows.ps1` performs this
check using locally synthesized speech. Add `-WithAI` to also test the configured
Gemini key and verify the resulting transcript and notes (uses the API).
