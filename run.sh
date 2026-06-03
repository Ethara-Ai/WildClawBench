#!/usr/bin/env bash
# WildClawBench end-to-end runner. Handles docker preflight, image loading from
# local tar, leaked-network cleanup, single-task or bulk K-run loops, and one
# docker-error retry. Designed to fail loudly with one signal at a time.
#
# Usage:
#   ./run.sh                                          # default: input/alden-croft_MB, claude-opus-4.7, K=1
#   ./run.sh <task-path>                              # one task, K=1
#   ./run.sh <task-path> <model>                      # one task, custom model
#   ./run.sh <task-path> <model> <K>                  # K runs of one task (pass@K)
#   ./run.sh --bulk <tasks-file> [model] [K]          # bulk: each line in tasks-file is a task path
#
# Exits non-zero on any preflight failure. Tees per-run logs into ./logs/.

set -u
set -o pipefail

readonly AGENT_IMAGE="wildclawbench-ubuntu:v1.3"
readonly AGENT_IMAGE_SHA="60eec8752cb597e180780ff08d7569c1892c169521f1f2b069c2efeb006a4078"
readonly AGENT_TAR_PATH="Images/wildclawbench-ubuntu_v1.3.tar"
readonly DEFAULT_TASK="input/alden-croft_MB"
readonly DEFAULT_MODEL="claude-opus-4.7"
readonly DEFAULT_K=1
readonly LOG_DIR="logs"

if [[ -t 1 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_YELLOW=$'\033[0;33m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_BOLD=$'\033[1m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED='' C_YELLOW='' C_GREEN='' C_BLUE='' C_BOLD='' C_RESET=''
fi

log_info()  { printf '%s[INFO]%s  %s\n' "$C_BLUE" "$C_RESET" "$*"; }
log_warn()  { printf '%s[WARN]%s  %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
log_err()   { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
log_ok()    { printf '%s[OK]%s    %s\n' "$C_GREEN" "$C_RESET" "$*"; }
log_step()  { printf '\n%s===%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }

preflight_docker() {
    log_step "Preflight: Docker daemon"
    if ! command -v docker >/dev/null 2>&1; then
        log_err "docker CLI not found in PATH"
        log_err "Install Docker Desktop from https://www.docker.com/products/docker-desktop"
        return 1
    fi
    if ! docker info >/dev/null 2>&1; then
        log_err "Docker daemon not responding (start Docker Desktop and retry)"
        return 1
    fi
    log_ok "Docker daemon up"
}

# Handles three failure modes:
#   1. Tag present and resolves: pass-through.
#   2. Tag missing but content image (by SHA) present: re-tag from SHA (b69 m1525 corruption).
#   3. Neither tag nor SHA present: try to load from AGENT_TAR_PATH; fail with HF hint if tar absent.
preflight_agent_image() {
    log_step "Preflight: Agent image ${AGENT_IMAGE}"

    if docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1; then
        log_ok "Image ${AGENT_IMAGE} present"
        return 0
    fi

    log_warn "Tag ${AGENT_IMAGE} not resolvable; checking for content image by SHA"
    if docker image inspect "sha256:${AGENT_IMAGE_SHA}" >/dev/null 2>&1; then
        log_warn "Content image present (sha256:${AGENT_IMAGE_SHA:0:16}...) but tag missing — re-tagging"
        if docker tag "sha256:${AGENT_IMAGE_SHA}" "$AGENT_IMAGE"; then
            log_ok "Re-tagged content image as ${AGENT_IMAGE}"
            return 0
        else
            log_err "docker tag failed"
            return 1
        fi
    fi

    log_warn "Content image also absent — looking for local tar at ${AGENT_TAR_PATH}"
    if [[ -f "$AGENT_TAR_PATH" ]]; then
        local tar_size
        tar_size=$(du -h "$AGENT_TAR_PATH" 2>/dev/null | awk '{print $1}')
        log_info "Loading ${AGENT_TAR_PATH} (${tar_size}) — this can take 2-15 min depending on disk"
        if command -v pv >/dev/null 2>&1; then
            pv "$AGENT_TAR_PATH" | docker load
        else
            log_warn "pv not installed; loading without progress display"
            docker load -i "$AGENT_TAR_PATH"
        fi
        if docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1; then
            log_ok "Image loaded successfully"
            return 0
        fi
        log_err "docker load completed but tag still not resolvable; check tar integrity"
        return 1
    fi

    log_err "Image not present and tar not found at ${AGENT_TAR_PATH}"
    log_err "To fetch the tar manually:"
    log_err "  hf download internlm/WildClawBench ${AGENT_TAR_PATH} --repo-type dataset --local-dir ."
    log_err "Then re-run this script."
    return 1
}

preflight_env_file() {
    log_step "Preflight: .env"
    if [[ ! -f .env ]]; then
        log_err ".env not found in $(pwd)"
        log_err "Copy .env.example to .env and fill in credentials"
        return 1
    fi

    local missing=()
    for key in KENSEI_AWS_BEARER_TOKEN KENSEI_AWS_REGION; do
        if ! grep -qE "^${key}=.+" .env; then
            missing+=("$key")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        log_warn ".env missing or empty: ${missing[*]} (Bedrock calls will fail if not set elsewhere)"
    fi
    log_ok ".env present"
}

# Removes containers and networks that survived a prior crashed batch.
# Names match the conventions used by start_litellm/start_mock_stack/
# _start_task_mock_stack — see (b69) for live cleanup precedent.
cleanup_orphans() {
    log_step "Cleanup: orphan containers and networks from prior runs"

    local containers
    containers=$(docker ps -aq --filter 'name=ll-' --filter 'name=mocks-' --filter 'name=t_' 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
        local count
        count=$(echo "$containers" | wc -l | tr -d ' ')
        log_warn "Found ${count} orphan container(s) — removing"
        echo "$containers" | xargs -r docker rm -f >/dev/null 2>&1 || true
    else
        log_info "No orphan containers"
    fi

    local networks
    networks=$(docker network ls --filter 'name=k3net-' -q 2>/dev/null || true)
    if [[ -n "$networks" ]]; then
        local count
        count=$(echo "$networks" | wc -l | tr -d ' ')
        log_warn "Found ${count} leaked k3net-* network(s) — removing"
        echo "$networks" | xargs -r -n1 docker network rm >/dev/null 2>&1 || true
    else
        log_info "No leaked networks"
    fi

    log_ok "Cleanup complete"
}

# Returns: stamps "$RUN_RC" and "$RUN_LOG" globals so the retry loop can inspect.
RUN_RC=0
RUN_LOG=""

run_one() {
    local task_path="$1"
    local model="$2"
    local run_index="$3"
    local total_runs="$4"

    local task_name
    task_name=$(basename "$task_path")
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    local model_safe="${model//\//_}"
    RUN_LOG="${LOG_DIR}/${task_name}_${model_safe}_run${run_index}_${ts}.log"
    mkdir -p "$LOG_DIR"

    log_step "Run ${run_index}/${total_runs}: ${task_name} × ${model}"
    log_info "Log: ${RUN_LOG}"

    set +e
    python3 eval/run_batch.py \
        --task "$task_path" \
        --agent-backend openclaw \
        --model "$model" \
        --litellm \
        --mock-stack \
        --generate-tests --testgen-max-attempts 3 \
        --execute-tests --testexec-timeout 600 \
        --thinking xhigh \
        --parallel 1 \
        --judge-council \
        2>&1 | tee "$RUN_LOG"
    RUN_RC=${PIPESTATUS[0]}
    set -e
}

# Single Docker-error retry: detects "Container startup failed", "manifest unknown",
# "Required Docker image not present" in the log, attempts a tag fix + cleanup,
# and reruns ONCE. Anything else → propagate.
is_docker_recoverable_error() {
    local log_file="$1"
    [[ -f "$log_file" ]] || return 1
    grep -qE 'Required Docker image not present|Container startup failed|No such image|manifest unknown' "$log_file" 2>/dev/null
}

attempt_docker_recovery() {
    log_step "Docker-recoverable error detected — attempting recovery"

    if docker image inspect "sha256:${AGENT_IMAGE_SHA}" >/dev/null 2>&1 && ! docker image inspect "$AGENT_IMAGE" >/dev/null 2>&1; then
        log_warn "Tag table appears corrupted — re-tagging from SHA"
        docker tag "sha256:${AGENT_IMAGE_SHA}" "$AGENT_IMAGE" || return 1
    fi

    cleanup_orphans
    log_ok "Recovery complete"
}

print_usage() {
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

main() {
    local mode="single"
    local tasks=()
    local model="$DEFAULT_MODEL"
    local k="$DEFAULT_K"

    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        print_usage
        exit 0
    fi

    if [[ "${1:-}" == "--bulk" ]]; then
        mode="bulk"
        shift
        local tasks_file="${1:-}"
        if [[ -z "$tasks_file" || ! -f "$tasks_file" ]]; then
            log_err "--bulk requires a tasks file argument (one task path per line)"
            exit 2
        fi
        while IFS= read -r line; do
            line="${line%%#*}"
            line="${line#"${line%%[![:space:]]*}"}"
            line="${line%"${line##*[![:space:]]}"}"
            [[ -n "$line" ]] && tasks+=("$line")
        done < "$tasks_file"
        model="${2:-$DEFAULT_MODEL}"
        k="${3:-$DEFAULT_K}"
    else
        tasks=("${1:-$DEFAULT_TASK}")
        model="${2:-$DEFAULT_MODEL}"
        k="${3:-$DEFAULT_K}"
    fi

    if ! [[ "$k" =~ ^[0-9]+$ ]] || (( k < 1 )); then
        log_err "K must be a positive integer, got: $k"
        exit 2
    fi

    log_step "WildClawBench runner"
    log_info "Mode:   $mode"
    log_info "Tasks:  ${#tasks[@]}"
    log_info "Model:  $model"
    log_info "K runs: $k per task"
    log_info "Cwd:    $(pwd)"

    preflight_docker || exit 1
    preflight_agent_image || exit 1
    preflight_env_file || exit 1
    cleanup_orphans

    local total=$(( ${#tasks[@]} * k ))
    local current=0
    local failed_count=0
    local recovered_count=0
    local failed_runs=()

    for task in "${tasks[@]}"; do
        if [[ ! -d "$task" && ! -f "$task" ]]; then
            log_err "Task path not found: $task — skipping"
            failed_count=$(( failed_count + k ))
            failed_runs+=("$task (path missing)")
            continue
        fi
        for (( i=1; i<=k; i++ )); do
            current=$(( current + 1 ))
            run_one "$task" "$model" "$current" "$total"

            if (( RUN_RC != 0 )); then
                if is_docker_recoverable_error "$RUN_LOG"; then
                    attempt_docker_recovery
                    log_step "Retry $current/$total after recovery"
                    run_one "$task" "$model" "$current" "$total"
                    if (( RUN_RC == 0 )); then
                        recovered_count=$(( recovered_count + 1 ))
                        log_ok "Run $current succeeded after recovery"
                    else
                        log_err "Run $current failed after recovery (rc=$RUN_RC)"
                        failed_count=$(( failed_count + 1 ))
                        failed_runs+=("$task#$i (rc=$RUN_RC after retry)")
                    fi
                else
                    log_err "Run $current failed (rc=$RUN_RC) — not a docker-recoverable error, no retry"
                    log_err "Tail of log:"
                    tail -n 20 "$RUN_LOG" >&2 || true
                    failed_count=$(( failed_count + 1 ))
                    failed_runs+=("$task#$i (rc=$RUN_RC)")
                fi
            else
                log_ok "Run $current completed"
            fi
        done
    done

    log_step "Summary"
    log_info "Total runs:      $total"
    log_info "Succeeded:       $(( total - failed_count ))"
    log_info "  of which retried successfully: $recovered_count"
    log_info "Failed:          $failed_count"

    if (( failed_count > 0 )); then
        log_err "Failed runs:"
        for r in "${failed_runs[@]}"; do
            log_err "  - $r"
        done
    fi

    if (( k > 1 || ${#tasks[@]} > 1 )); then
        log_step "Aggregating pass@K"
        if python3 scripts/aggregate_runs.py --backend openclaw 2>&1; then
            log_ok "Aggregated → output/openclaw_aggregate_summary.json"
        else
            log_warn "aggregate_runs.py failed; pass@K rollup unavailable"
        fi
    fi

    if (( failed_count > 0 )); then
        exit 1
    fi
    log_ok "All runs completed successfully"
    exit 0
}

main "$@"
