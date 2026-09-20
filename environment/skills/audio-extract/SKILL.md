---
name: audio-extract
description: Extract a 16kHz mono WAV audio track from any media file, probe metadata, and transcribe speech to text. transcribe.sh walks whisper.cpp -> the harness LiteLLM sidecar -> local openai-whisper and uses whichever exist (no internet required from inside the agent container).
metadata: {"clawdbot":{"emoji":"🎧","requires":{"bins":["ffmpeg","ffprobe","curl"],"env":[]},"install":[{"id":"brew","kind":"brew","formula":"ffmpeg","bins":["ffmpeg"],"label":"Install ffmpeg (brew)"}]}}
---

# Audio Extract & Transcribe

This skill turns any audio/video file into (a) a clean 16kHz mono WAV and
(b) a plain-text transcript. Both steps run entirely inside the sandbox: the
WAV is produced locally by `ffmpeg`, and the transcript comes from whichever
engine the image and the run provide: a local `whisper.cpp`, the
harness-provided LiteLLM sidecar over the internal Docker bridge, or the local
`openai-whisper` package baked into the agent image. The agent container has
**no direct internet access** — do NOT try `pip install openai-whisper` or
`curl https://api.openai.com/...`; those fail with `Temporary failure in name
resolution`. `transcribe.sh` picks the right path for you and falls to the next
one if an engine fails.

## Quick start — transcribe (most common case)

```bash
{baseDir}/scripts/transcribe.sh /path/to/recording.m4a
```

Output (stdout): the transcript text, one block.
The intermediate WAV is left at `/tmp_workspace/_scratch/<basename>.wav` so
you can re-use it (e.g. send a second pass with different prompt context).

## Probe only (metadata, no extraction, no transcription)

```bash
{baseDir}/scripts/extract.sh --probe /path/to/recording.mp4
```

Prints duration, format, codecs, stream count.

## Extract only (no transcription) — useful when you want to control the WAV path

```bash
{baseDir}/scripts/extract.sh /path/to/recording.mp4 /tmp_workspace/results/audio.wav
```

The 16kHz mono WAV is the standard input format for speech-to-text. If you
have already produced a WAV yourself, you can transcribe it directly:

```bash
{baseDir}/scripts/transcribe.sh /tmp_workspace/results/audio.wav
```

## How transcription works (so you can debug if it fails)

`transcribe.sh` always first re-encodes the input to 16kHz mono WAV with
`ffmpeg`, then walks this ladder, moving to the next rung whenever one is
absent **or fails**:

1. **whisper.cpp (offline, fast):** if `whisper-cli` is on `PATH` and the model
   at `$WCB_WHISPER_MODEL` (default `/opt/whisper-models/ggml-base.en.bin`)
   exists, it runs `whisper-cli -m <model> -f <wav> -otxt`.
2. **Sidecar (fast, needs the route):** if `$WCB_AUDIO_TRANSCRIBE_URL` is set it
   `POST`s a multipart form to the LiteLLM sidecar `/v1/audio/transcriptions`
   (model `whisper-1`) and extracts `.text`. The harness only sets the URL when
   the run's LiteLLM config registers `whisper-1` (openclaw backend).
3. **openai-whisper (offline, CPU, slowest):** if the `whisper` Python package
   imports and `$WCB_WHISPER_MODEL_DIR` (default `/opt/wb_whisper_models`)
   exists, it runs the baked `small` model. Expect roughly real-time speed —
   a 10-minute recording can take several minutes.

Force a side with `WCB_TRANSCRIBE_ENGINE=local` (rungs 1 and 3) or `=sidecar`
(rung 2 only).

Sidecar request shape (rung 2):

```bash
curl -s --fail \
  -H "Authorization: Bearer $WCB_AUDIO_TRANSCRIBE_AUTH" \
  -F "file=@/path/to/audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=json" \
  "$WCB_AUDIO_TRANSCRIBE_URL"
```

If NO rung is available, `transcribe.sh` prints which pieces are missing and
exits 3. That means the harness neither baked a local engine into this image
nor wired the sidecar (a configuration regression — flag it in your final
answer rather than silently dropping the audio content).

If the sidecar POST fails (network error, sidecar down, HTTP 4xx/5xx),
`transcribe.sh` prints the curl error and the response body, then falls back to
local openai-whisper when it is present; only with no rung left does it exit
non-zero (5 transport, 6 non-200, 8 local whisper failed). Common causes:

- `Could not resolve host` — sidecar container name is unreachable from
  this agent container. The bridge network was not created or the sidecar
  is not joined. Treat as a harness bug.
- `HTTP 401/403` from upstream — the sidecar has no valid upstream API
  key. Treat as a harness bug; do not attempt to call OpenAI directly
  from the agent container (no internet egress).
- `HTTP 400 Invalid model name` — the sidecar config did not register
  `whisper-1`. Treat as a harness bug.

## Local engines

Two local engines are supported because images differ: `whisper.cpp`
(`whisper-cli` + a `ggml` model; English-only `base.en` by default) and
`openai-whisper` (`small`, multilingual), which `docker/agent-whisper.Dockerfile`
bakes into `wildclawbench-ubuntu:v1.4` / `v1.6` and the delivery bundle's
Dockerfile bakes too. Both are CPU, single-pass (no diarization/timestamps) and
fully offline. To call openai-whisper directly on a WAV you already have:

```python
import whisper
model = whisper.load_model("small", download_root="/opt/wb_whisper_models")
print(model.transcribe("/path/to/audio.wav")["text"])
```

The two openclaw-runtime `whisper` skills (`openai-whisper`,
`openai-whisper-api`) still do NOT work in this sandbox (no `whisper` CLI on
`PATH` / no `api.openai.com`); prefer `transcribe.sh` over both.

## Requires

- `ffmpeg`, `ffprobe` — installed in the image. Used by both
  `extract.sh` and `transcribe.sh`.
- At least ONE transcription engine (the script reports which are missing):
  - `whisper-cli` + model at `/opt/whisper-models/ggml-base.en.bin`
    (override with `WCB_WHISPER_MODEL`), or
  - `WCB_AUDIO_TRANSCRIBE_URL` / `WCB_AUDIO_TRANSCRIBE_AUTH` — exported by the
    harness (openclaw backend) when the sidecar registers `whisper-1`, or
  - the `whisper` Python package + weights under `/opt/wb_whisper_models`
    (override with `WCB_WHISPER_MODEL_DIR`).
- `curl` — installed in the image. Used by the sidecar rung.
- `WCB_TRANSCRIBE_ENGINE` — OPTIONAL; force `local` or `sidecar`.
