param([switch]$WithAI)

# Hardware check: synthesize known speech, play it through the default output,
# capture loopback in a detached process, then optionally transcribe with Gemini.
# Run from the repository root. Outputs stay in ignored recordings/.
$ErrorActionPreference = 'Stop'
$smokeRoot = Join-Path (Get-Location) 'recordings/hardware-smoke'
New-Item -ItemType Directory -Force -Path $smokeRoot | Out-Null
$fixture = Join-Path $smokeRoot 'speech-fixture.wav'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.SetOutputToWaveFile($fixture)
    $voice.Speak('Welcome to the project meeting. We decided to send the proposal on Friday. Alex will prepare the draft. The next meeting is on Monday.')
} finally {
    $voice.Dispose()
}

$startArguments = @('start', '--duration', '60', '--chunk-seconds', '15')
if (-not $WithAI) { $startArguments += '--record-only' }
uv run ai-note-taker --output $smokeRoot @startArguments
if ($LASTEXITCODE -ne 0) { throw 'Background recorder failed to start.' }
try {
    $player = New-Object System.Media.SoundPlayer $fixture
    $player.PlaySync()
    $player.Dispose()
    Start-Sleep -Milliseconds 500
} finally {
    uv run ai-note-taker --output $smokeRoot stop --wait 10
}
if ($LASTEXITCODE -ne 0) { throw 'Recorder did not stop cleanly.' }
$pointer = Get-Content (Join-Path $smokeRoot 'current.json') -Raw | ConvertFrom-Json
$session = Join-Path $smokeRoot $pointer.session_id
$audioFiles = @(Get-ChildItem (Join-Path $session 'audio') -Filter '*.wav')
if ($audioFiles.Count -eq 0) { throw 'No audio was captured from the default output.' }
$state = Get-Content (Join-Path $session 'session.json') -Raw | ConvertFrom-Json
if ($state.status -ne 'stopped') { throw "Capture failed: $($state.error_type)" }
Write-Output "PASS: detached recording, stop, and WAV persistence ($($audioFiles.Count) chunks)."

if ($WithAI) {
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    while ([DateTime]::UtcNow -lt $deadline) {
        $status = uv run ai-note-taker --output $smokeRoot status | ConvertFrom-Json
        if (-not $status.process_alive) { break }
        Write-Output 'Waiting for background transcription and notes...'
        Start-Sleep -Seconds 2
    }
    if ($status.process_alive) { throw 'Background processing did not finish within five minutes.' }
    if ($status.processing.status -ne 'complete') {
        throw 'AI processing failed; check processing.json and errors/.'
    }
    $transcript = Get-Content (Join-Path $session 'transcript.txt') -Raw
    if ($transcript -notmatch 'proposal') { throw 'Transcript did not contain the expected spoken word.' }
    $notes = Get-Content (Join-Path $session 'notes.json') -Raw | ConvertFrom-Json
    if (-not $notes.summary) { throw 'AI meeting notes were empty.' }
    Write-Output 'PASS: spoken fixture transcribed and structured notes saved.'
}
Write-Output "Session: $session"
