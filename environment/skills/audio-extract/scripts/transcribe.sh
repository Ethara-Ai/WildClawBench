#!/usr/bin/env bash
# Transcribe an audio/video file to text. Prefers a local offline whisper.cpp
# engine baked into the agent image; falls back to the harness LiteLLM sidecar.
#
# Usage:
#   transcribe.sh <media>                 # audio OR video; auto-extracts WAV
#   transcribe.sh <media> --raw           # also print raw engine/sidecar detail to stderr
#
# Output (stdout): the transcript text.
# Output (stderr): step markers ("== extract ==", "== transcribe (local|sidecar) ==").
#
# Exit codes:
#   0  success
#   1  input file not found
#   2  usage / --help
#   3  no transcription path available (no local whisper.cpp AND no sidecar URL)
#   4  ffmpeg produced no audio
#   5  curl transport error (sidecar path)
#   6  sidecar returned non-200 (sidecar path)
#   7  response/engine produced no text
#
# Engine selection:
#   Local first: whisper-cli on PATH + model at WCB_WHISPER_MODEL
#   (default /opt/whisper-models/ggml-base.en.bin, baked by image_overlays/agent-whisper.Dockerfile).
#   Works fully offline on every backend. Falls back to the sidecar
#   /v1/audio/transcriptions endpoint (WCB_AUDIO_TRANSCRIBE_URL, openclaw-only) when
#   the local engine is absent. Force one path with WCB_TRANSCRIBE_ENGINE=local|sidecar.
#
# The ffmpeg -> 16kHz mono pcm_s16le re-encode is shared by both paths: it is the
# format whisper.cpp requires and keeps sidecar uploads under OpenAI's 25 MB cap.
# Response is parsed with python3 (jq is NOT in wildclawbench-ubuntu:v1.3).

set -euo pipefail

if [[ "${1:-}" == "" || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat >&2 <<'EOF'
usage: transcribe.sh <media> [--raw]

Transcribe an audio or video file to text. Uses a local offline whisper.cpp
engine when available, otherwise the harness LiteLLM sidecar. The transcript is
printed on stdout. With --raw, engine/response detail is also printed on stderr.
EOF
  exit 2
fi

in="$1"
raw_mode="0"
if [[ "${2:-}" == "--raw" ]]; then
  raw_mode="1"
fi

if [[ ! -f "$in" ]]; then
  echo "transcribe.sh: input file not found: $in" >&2
  exit 1
fi

whisper_model="${WCB_WHISPER_MODEL:-/opt/whisper-models/ggml-base.en.bin}"
forced_engine="${WCB_TRANSCRIBE_ENGINE:-}"
sidecar_url="${WCB_AUDIO_TRANSCRIBE_URL:-}"

local_available="0"
if command -v whisper-cli >/dev/null 2>&1 && [[ -s "$whisper_model" ]]; then
  local_available="1"
fi

use_local="0"
case "$forced_engine" in
  local)
    if [[ "$local_available" != "1" ]]; then
      echo "transcribe.sh: WCB_TRANSCRIBE_ENGINE=local but whisper-cli or model ($whisper_model) is missing." >&2
      exit 3
    fi
    use_local="1"
    ;;
  sidecar)
    use_local="0"
    ;;
  "")
    use_local="$local_available"
    ;;
  *)
    echo "transcribe.sh: invalid WCB_TRANSCRIBE_ENGINE='$forced_engine' (want local|sidecar)." >&2
    exit 2
    ;;
esac

if [[ "$use_local" != "1" && -z "$sidecar_url" ]]; then
  echo "transcribe.sh: no transcription path available." >&2
  echo "  Local: whisper-cli + model ($whisper_model) not found (bake image_overlays/agent-whisper.Dockerfile)." >&2
  echo "  Sidecar: WCB_AUDIO_TRANSCRIBE_URL unset (injected by openclaw runner only)." >&2
  echo "  Treat as a harness configuration regression." >&2
  exit 3
fi

basename_in="$(basename "$in")"
stem="${basename_in%.*}"
scratch_dir="/tmp_workspace/_scratch"
mkdir -p "$scratch_dir"
wav="$scratch_dir/${stem}.wav"

echo "== extract: $in -> $wav ==" >&2
ffmpeg -y -loglevel error -i "$in" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$wav"

if [[ ! -s "$wav" ]]; then
  echo "transcribe.sh: ffmpeg produced no audio output for $in" >&2
  exit 4
fi

if [[ "$use_local" == "1" ]]; then
  echo "== transcribe (local): whisper-cli -m $whisper_model ==" >&2
  txt_base="$scratch_dir/${stem}"
  if [[ "$raw_mode" == "1" ]]; then
    whisper-cli -m "$whisper_model" -f "$wav" -otxt -of "$txt_base" >&2
  else
    whisper-cli -m "$whisper_model" -f "$wav" -otxt -of "$txt_base" --no-prints
  fi
  txt="${txt_base}.txt"
  if [[ ! -s "$txt" ]]; then
    echo "transcribe.sh: whisper-cli produced no transcript text for $in" >&2
    exit 7
  fi
  cat "$txt"
  if [[ -n "$(tail -c1 "$txt")" ]]; then
    echo
  fi
  exit 0
fi

echo "== transcribe (sidecar): POST $sidecar_url (file=$wav, model=whisper-1) ==" >&2

resp_body="$(mktemp)"
trap 'rm -f "$resp_body"' EXIT

auth_header=()
if [[ -n "${WCB_AUDIO_TRANSCRIBE_AUTH:-}" ]]; then
  auth_header=(-H "Authorization: Bearer ${WCB_AUDIO_TRANSCRIBE_AUTH}")
fi
if [[ -n "${WCB_RUN_KEY:-}" ]]; then
  auth_header+=(-H "x-wcb-run-key: ${WCB_RUN_KEY}")
fi

http_code="$(curl -sS \
  -w "%{http_code}" \
  -o "$resp_body" \
  "${auth_header[@]}" \
  -F "file=@${wav}" \
  -F "model=whisper-1" \
  -F "response_format=json" \
  "$sidecar_url")" || {
    echo "transcribe.sh: curl transport error reaching $sidecar_url" >&2
    echo "  Response body (if any):" >&2
    sed 's/^/    /' < "$resp_body" >&2 || true
    exit 5
  }

if [[ "$http_code" != "200" ]]; then
  echo "transcribe.sh: sidecar returned HTTP $http_code from $sidecar_url" >&2
  echo "  Response body:" >&2
  sed 's/^/    /' < "$resp_body" >&2 || true
  exit 6
fi

if [[ "$raw_mode" == "1" ]]; then
  echo "== raw response ==" >&2
  cat "$resp_body" >&2
  echo >&2
fi

python3 - "$resp_body" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
text = data.get("text")
if text is None:
    sys.stderr.write(
        "transcribe.sh: sidecar response did not contain 'text' field.\n"
        f"  Keys present: {list(data.keys())}\n"
    )
    sys.exit(7)
sys.stdout.write(text)
if not text.endswith("\n"):
    sys.stdout.write("\n")
PY
