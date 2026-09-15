---
name: audio-extract
description: Extract a 16kHz mono WAV audio track from any media file, probe metadata, and transcribe speech to text. Uses a local offline whisper.cpp engine baked into the agent image when available, otherwise the harness-provided LiteLLM sidecar (no internet required from inside the agent container).
metadata: {"clawdbot":{"emoji":"🎧","requires":{"bins":["ffmpeg","ffprobe","curl"],"env":[]},"install":[{"id":"brew","kind":"brew","formula":"ffmpeg","bins":["ffmpeg"],"label":"Install ffmpeg (brew)"}]}}
---

# Audio Extract & Transcribe

This skill turns any audio/video file into (a) a clean 16kHz mono WAV and
(b) a plain-text transcript. Both steps run entirely inside the sandbox: the
WAV is produced locally by `ffmpeg`, and the transcript is produced by a
local offline `whisper.cpp` engine baked into the agent image when available,
falling back to the harness-provided LiteLLM sidecar over the internal Docker
bridge. The agent container has **no direct internet access** — do NOT try
`pip install openai-whisper` or `curl https://api.openai.com/...`; those fail
with `Temporary failure in name resolution`. `transcribe.sh` picks the right
path for you.

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
`ffmpeg`, then picks an engine:

1. **Local (preferred, offline):** if `whisper-cli` is on `PATH` and the model
   at `$WCB_WHISPER_MODEL` (default `/opt/whisper-models/ggml-base.en.bin`)
   exists, it runs `whisper-cli -m <model> -f <wav> -otxt` and prints the
   resulting text. No network, works on every backend.
2. **Sidecar (fallback):** otherwise it `POST`s a multipart form to
   `$WCB_AUDIO_TRANSCRIBE_URL` (LiteLLM sidecar `/v1/audio/transcriptions`,
   model `whisper-1`, wired only by the openclaw backend) and extracts
   `.text` from the JSON response.

Force a path with `WCB_TRANSCRIBE_ENGINE=local` or `=sidecar`.

Sidecar request shape (fallback path):

```bash
curl -s --fail \
  -H "Authorization: Bearer $WCB_AUDIO_TRANSCRIBE_AUTH" \
  -F "file=@/path/to/audio.wav" \
  -F "model=whisper-1" \
  -F "response_format=json" \
  "$WCB_AUDIO_TRANSCRIBE_URL"
```

If NEITHER path is available (no `whisper-cli`/model AND no
`WCB_AUDIO_TRANSCRIBE_URL`), `transcribe.sh` prints a clear error and exits 3.
That means the harness neither baked the local engine nor wired the sidecar (a
configuration regression — flag it in your final answer rather than silently
dropping the audio content).

If the sidecar POST fails (network error, sidecar down, HTTP 4xx/5xx),
`transcribe.sh` prints the curl error and the response body, then exits
non-zero. Common causes:

- `Could not resolve host` — sidecar container name is unreachable from
  this agent container. The bridge network was not created or the sidecar
  is not joined. Treat as a harness bug.
- `HTTP 401/403` from upstream — the sidecar has no valid upstream API
  key. Treat as a harness bug; do not attempt to call OpenAI directly
  from the agent container (no internet egress).
- `HTTP 400 Invalid model name` — the sidecar config did not register
  `whisper-1`. Treat as a harness bug.

## Local engine (whisper.cpp)

The local engine is `whisper.cpp` (`whisper-cli` + `ggml-base.en` model),
baked into the agent image by `image_overlays/agent-whisper.Dockerfile`. It is
English-only, CPU, single-pass (no chunking/diarization/timestamps), and fully
offline. The two openclaw-runtime `whisper` skills (`openai-whisper`,
`openai-whisper-api`) still do NOT work in this sandbox (no CLI binary / no
`api.openai.com`); `transcribe.sh`'s local path supersedes them.

## Requires

- `ffmpeg`, `ffprobe` — installed in the image. Used by both
  `extract.sh` and `transcribe.sh`.
- `whisper-cli` + model at `/opt/whisper-models/ggml-base.en.bin` — baked by
  `image_overlays/agent-whisper.Dockerfile` for the local (preferred) path. Override
  the model path with `WCB_WHISPER_MODEL`.
- `curl` — installed in the image. Used by the sidecar fallback path.
- `WCB_AUDIO_TRANSCRIBE_URL` / `WCB_AUDIO_TRANSCRIBE_AUTH` — OPTIONAL; exported
  by the harness (openclaw backend only) for the sidecar fallback. Not needed
  when the local engine is present.
- `WCB_TRANSCRIBE_ENGINE` — OPTIONAL; force `local` or `sidecar`.
