#!/usr/bin/env bash
# Host-side judge audio transcription (src/utils/judge_asr.py): install the
# optional python deps and fetch the sherpa-onnx model into ~/.wcb/asr.
#
#   bash script/setup_judge_asr.sh            # install + fetch + verify
#   bash script/setup_judge_asr.sh --check    # report readiness only, change nothing
#
# Idempotent: a model dir that already holds *.onnx + tokens.txt is left alone.
# Env: WCB_JUDGE_ASR_MODEL_DIR (target dir, default ~/.wcb/asr)
#      WCB_JUDGE_ASR_MODEL_URL (archive to fetch; a .tar.bz2 with one top-level dir)
set -u
set -o pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

MODEL_DIR="${WCB_JUDGE_ASR_MODEL_DIR:-$HOME/.wcb/asr}"
MODEL_URL="${WCB_JUDGE_ASR_MODEL_URL:-https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2}"
PY="python3"
[[ -x .venv/bin/python ]] && PY=".venv/bin/python"

check() {
    WCB_JUDGE_ASR_MODEL_DIR="$MODEL_DIR" "$PY" - <<'PYEOF'
import sys
from src.utils import judge_asr
ok, detail = judge_asr.status()
print(f"judge ASR: {'ready' if ok else 'UNAVAILABLE'} ({detail})")
sys.exit(0 if ok else 1)
PYEOF
}

if [[ "${1:-}" == "--check" ]]; then
    check
    exit $?
fi

echo "[judge-asr] installing python deps (requirements-asr.txt) with $PY"
"$PY" -m pip install -q -r requirements-asr.txt || { echo "[judge-asr] pip install failed" >&2; exit 1; }

if compgen -G "$MODEL_DIR/*.onnx" >/dev/null && [[ -f "$MODEL_DIR/tokens.txt" ]]; then
    echo "[judge-asr] model already present in $MODEL_DIR"
else
    echo "[judge-asr] fetching model (~480 MB download, ~640 MB on disk) into $MODEL_DIR"
    mkdir -p "$MODEL_DIR" || exit 1
    tmp="$(mktemp "$MODEL_DIR/.model.XXXXXX.tar.bz2")"
    trap 'rm -f "$tmp"' EXIT
    curl -L --fail --retry 3 -o "$tmp" "$MODEL_URL" || { echo "[judge-asr] download failed: $MODEL_URL" >&2; exit 1; }
    tar -xjf "$tmp" -C "$MODEL_DIR" --strip-components=1 || { echo "[judge-asr] extract failed" >&2; exit 1; }
fi

check || exit 1

# Prove it end to end on the sample shipped with the model, when there is one.
sample="$MODEL_DIR/test_wavs/en.wav"
if [[ -f "$sample" ]]; then
    WCB_JUDGE_ASR_MODEL_DIR="$MODEL_DIR" "$PY" - "$sample" <<'PYEOF'
import sys
from pathlib import Path
from src.utils import judge_asr
text = judge_asr.transcribe(Path(sys.argv[1]))
if not text:
    print("[judge-asr] sample transcription returned nothing", file=sys.stderr)
    sys.exit(1)
print(f"[judge-asr] sample transcript: {text[:120]}")
PYEOF
fi
