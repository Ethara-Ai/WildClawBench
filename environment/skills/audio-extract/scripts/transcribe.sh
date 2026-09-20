#!/usr/bin/env bash
# Transcribe an audio/video file to text. Walks a ladder of engines and falls to
# the next rung whenever one is absent or fails, so a run only loses the audio
# when NO path exists.
#
# Usage:
#   transcribe.sh <media>                 # print transcript on stdout
#   transcribe.sh <media> --raw           # also print raw engine/sidecar detail to stderr
#
# Output (stdout): the transcribed text.
# Output (stderr): step markers ("== extract ==", "== transcribe (<engine>) ==").
#
# Exit codes:
#   0  success
#   1  input file not found
#   2  usage / --help / invalid WCB_TRANSCRIBE_ENGINE
#   3  no transcription path available (no local engine AND no sidecar URL)
#   4  ffmpeg produced no audio
#   5  curl transport error (sidecar was the last rung)
#   6  sidecar returned non-200 (sidecar was the last rung)
#   7  response/engine produced no text
#   8  local openai-whisper transcription failed (it was the last rung)
#
# Engine ladder (auto):
#   1. whisper.cpp   whisper-cli on PATH + ggml model at WCB_WHISPER_MODEL
#                    (default /opt/whisper-models/ggml-base.en.bin). Fast, offline.
#   2. sidecar       POST to WCB_AUDIO_TRANSCRIBE_URL (LiteLLM /v1/audio/transcriptions,
#                    model whisper-1; wired by the openclaw backend only when the
#                    run's LiteLLM config registers whisper-1). Fast, needs the route.
#   3. openai-whisper  python `whisper` + 'small' weights under WCB_WHISPER_MODEL_DIR
#                    (default /opt/wb_whisper_models) — what docker/agent-whisper.Dockerfile
#                    and the delivery bundle's Dockerfile bake. Offline, CPU, slowest,
#                    so it sits below the sidecar.
# Force a side with WCB_TRANSCRIBE_ENGINE=local (rungs 1+3 only) or =sidecar (rung 2 only).
#
# The ffmpeg -> 16kHz mono pcm_s16le re-encode is shared by every rung: it is the
# format whisper.cpp requires and keeps sidecar uploads under OpenAI's 25 MB cap.
# Responses are parsed with python3 (jq is NOT in wildclawbench-ubuntu:v1.3).

set -euo pipefail

if [[ "${1:-}" == "" || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat >&2 <<'EOF'
usage: transcribe.sh <media> [--raw]

Transcribe an audio or video file to text. Tries a local whisper.cpp engine,
then the harness LiteLLM sidecar, then local openai-whisper, using whichever
exist. The transcript is printed on stdout. With --raw, engine/response detail
is also printed on stderr.
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
whisper_model_dir="${WCB_WHISPER_MODEL_DIR:-/opt/wb_whisper_models}"
forced_engine="${WCB_TRANSCRIBE_ENGINE:-}"
sidecar_url="${WCB_AUDIO_TRANSCRIBE_URL:-}"

have_cpp="0"
if command -v whisper-cli >/dev/null 2>&1 && [[ -s "$whisper_model" ]]; then
  have_cpp="1"
fi
# The weights dir alone is not enough: a bind-mounted dir on an image without the
# package must not claim the rung and then die inside python.
have_py="0"
if [[ -d "$whisper_model_dir" ]] && python3 -c "import whisper" >/dev/null 2>&1; then
  have_py="1"
fi
have_sidecar="0"
if [[ -n "$sidecar_url" ]]; then
  have_sidecar="1"
fi

case "$forced_engine" in
  local)
    have_sidecar="0"
    if [[ "$have_cpp" != "1" && "$have_py" != "1" ]]; then
      echo "transcribe.sh: WCB_TRANSCRIBE_ENGINE=local but no local engine is present." >&2
      echo "  whisper.cpp: whisper-cli or model ($whisper_model) missing." >&2
      echo "  openai-whisper: python package or weights dir ($whisper_model_dir) missing." >&2
      exit 3
    fi
    ;;
  sidecar)
    have_cpp="0"
    have_py="0"
    ;;
  "")
    ;;
  *)
    echo "transcribe.sh: invalid WCB_TRANSCRIBE_ENGINE='$forced_engine' (want local|sidecar)." >&2
    exit 2
    ;;
esac

if [[ "$have_cpp" != "1" && "$have_py" != "1" && "$have_sidecar" != "1" ]]; then
  echo "transcribe.sh: no transcription path available." >&2
  echo "  whisper.cpp: whisper-cli + model ($whisper_model) not found." >&2
  echo "  Sidecar: WCB_AUDIO_TRANSCRIBE_URL unset (injected by the openclaw runner only when whisper-1 is registered)." >&2
  echo "  openai-whisper: python package or weights dir ($whisper_model_dir) not found (bake docker/agent-whisper.Dockerfile)." >&2
  exit 3
fi

basename_in="$(basename "$in")"
stem="${basename_in%.*}"
scratch_dir="${WCB_TRANSCRIBE_SCRATCH_DIR:-/tmp_workspace/_scratch}"
mkdir -p "$scratch_dir"
wav="$scratch_dir/${stem}.wav"

echo "== extract: $in -> $wav ==" >&2
ffmpeg -y -loglevel error -i "$in" -vn -acodec pcm_s16le -ar 16000 -ac 1 "$wav"

if [[ ! -s "$wav" ]]; then
  echo "transcribe.sh: ffmpeg produced no audio output for $in" >&2
  exit 4
fi

resp_body="$(mktemp)"
trap 'rm -f "$resp_body"' EXIT
last_rc=3

# Each rung prints the transcript on stdout and returns 0, or returns the exit
# code the script should die with if no later rung rescues it.

transcribe_cpp() {
  echo "== transcribe (whisper.cpp): whisper-cli -m $whisper_model ==" >&2
  local txt_base="$scratch_dir/${stem}" txt
  rm -f "${txt_base}.txt"
  if [[ "$raw_mode" == "1" ]]; then
    whisper-cli -m "$whisper_model" -f "$wav" -otxt -of "$txt_base" >&2 || true
  else
    whisper-cli -m "$whisper_model" -f "$wav" -otxt -of "$txt_base" --no-prints || true
  fi
  txt="${txt_base}.txt"
  if [[ ! -s "$txt" ]]; then
    echo "transcribe.sh: whisper-cli produced no transcript text for $in" >&2
    return 7
  fi
  cat "$txt"
  if [[ -n "$(tail -c1 "$txt")" ]]; then
    echo
  fi
  return 0
}

transcribe_sidecar() {
  echo "== transcribe (sidecar): POST $sidecar_url (file=$wav, model=whisper-1) ==" >&2
  local auth_header=() http_code
  if [[ -n "${WCB_AUDIO_TRANSCRIBE_AUTH:-}" ]]; then
    auth_header=(-H "Authorization: Bearer ${WCB_AUDIO_TRANSCRIBE_AUTH}")
  fi
  if [[ -n "${WCB_RUN_KEY:-}" ]]; then
    auth_header+=(-H "x-wcb-run-key: ${WCB_RUN_KEY}")
  fi

  # Split status from body (-w/-o) so a non-2xx body can still be shown.
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
      return 5
    }

  if [[ "$http_code" != "200" ]]; then
    echo "transcribe.sh: sidecar returned HTTP $http_code from $sidecar_url" >&2
    echo "  Response body:" >&2
    sed 's/^/    /' < "$resp_body" >&2 || true
    return 6
  fi

  if [[ "$raw_mode" == "1" ]]; then
    echo "== raw response ==" >&2
    cat "$resp_body" >&2
    echo >&2
  fi

  python3 - "$resp_body" <<'PY' || return 7
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
  return 0
}

transcribe_py() {
  echo "== transcribe (openai-whisper): small, $whisper_model_dir ==" >&2
  python3 - "$wav" "$whisper_model_dir" <<'PY' || return 8
import sys
import whisper
wav, model_dir = sys.argv[1], sys.argv[2]
model = whisper.load_model("small", download_root=model_dir)
text = model.transcribe(wav)["text"].strip()
if not text:
    sys.stderr.write("transcribe.sh: openai-whisper produced no transcript text.\n")
    sys.exit(7)
sys.stdout.write(text + "\n")
PY
  return 0
}

for rung in cpp sidecar py; do
  have="have_${rung}"
  if [[ "${!have}" != "1" ]]; then
    continue
  fi
  # Buffer the rung's stdout: a rung that fails midway must not leak a partial
  # transcript ahead of the rung that succeeds.
  out="$(mktemp)"
  if "transcribe_${rung}" >"$out"; then
    cat "$out"
    rm -f "$out"
    exit 0
  else
    last_rc=$?
  fi
  rm -f "$out"
  echo "transcribe.sh: ${rung} rung failed (rc=$last_rc); trying the next available engine." >&2
done

echo "transcribe.sh: every available transcription engine failed for $in" >&2
exit "$last_rc"
