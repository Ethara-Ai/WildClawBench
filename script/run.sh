#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  cat <<'EOF'
Usage:
  bash script/run.sh openclaw    [run_batch args...]
  bash script/run.sh claudecode  [run_batch args...]
  bash script/run.sh codex       [run_batch args...]
  bash script/run.sh hermesagent [run_batch args...]

Examples:
  bash script/run.sh openclaw --category all --parallel 4 --model openrouter/openai/gpt-5.5
  bash script/run.sh claudecode --category all --parallel 4 --model openai/gpt-5.5
  bash script/run.sh codex --category all --parallel 4 --model openrouter/openai/gpt-5.5
  bash script/run.sh hermesagent --category all --parallel 4 --model openai/gpt-5.5

  bash script/run.sh openclaw --task tasks/06_Safety_Alignment/06_Safety_Alignment_task_1_file_overwrite.md --model openrouter/openai/gpt-5.5
EOF
  exit 1
fi

backend="$1"
shift || true

case "$backend" in
  openclaw|claudecode|codex|hermesagent) ;;
  *)
    echo "Unknown backend: $backend"
    echo "Expected one of: openclaw, claudecode, codex, hermesagent"
    exit 1
    ;;
esac

task_slug="batch"
model_slug="default"
prev=""
for arg in "$@"; do
  case "$prev" in
    --task)
      task_slug=$(basename "${arg%/}" | tr -c '[:alnum:]' '_' | sed 's/_\+/_/g; s/^_//; s/_$//')
      ;;
    --model)
      model_slug=$(echo "$arg" | tr -c '[:alnum:]' '_' | sed 's/_\+/_/g; s/^_//; s/_$//')
      ;;
    --category)
      task_slug="cat_${arg}"
      ;;
  esac
  prev="$arg"
done

mkdir -p logs
log_path="logs/${backend}_${task_slug}_${model_slug}_$(date +%Y%m%d_%H%M%S).log"
echo "[run.sh] logging to $log_path" >&2

exec python3 eval/run_batch.py --agent-backend "$backend" "$@" 2>&1 | tee "$log_path"
