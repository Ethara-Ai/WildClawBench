#!/usr/bin/env bash
# WildClawBench end-to-end runner. Handles docker preflight, image loading from
# local tar, leaked-network cleanup, single-task or bulk K-run loops, and one
# docker-error retry. Designed to fail loudly with one signal at a time.
#
# Usage:
#   ./run.sh                                                # default: input/alden-croft_MB, claude-opus-4.8, K=1
#   ./run.sh <task-path>                                    # one task, K=1
#   ./run.sh <task-path> <model[,model2,...]>               # one task, one or more models (parallel)
#   ./run.sh <task-path> <model[,model2,...]> <K>           # K sequential runs per model; models run in parallel
#   ./run.sh --bulk <tasks-file> [model[,model2,...]] [K]   # bulk: tasks sequential, models parallel per task
#   ./run.sh --bulk <tasks-file> [model] [K] --jobs 4       # bulk: run up to 4 TASKS in parallel (safe)
#   ./run.sh --regrade <run-dir> [--rubric <path>]          # re-judge a completed run; overwrites score.json
#
# Exits non-zero on any preflight failure. Tees per-run logs into ./logs/.
# Multiple models comma-separated (no spaces) run in parallel for the same task;
# K runs of the same (task,model) are sequential.
# --jobs N runs up to N tasks concurrently (default 1 = sequential). Each task
#   gets its own mock stack + k3net network, so they coexist; the orphan cleanup
#   only touches stopped containers / empty networks, never a live run.
# --no-subagents forces sub-agent spawning OFF for the run(s): the model is never
#   offered sessions_spawn, so it does the whole task inline (no fan-out). Works
#   with single, --bulk, and combined with --jobs.
# --regrade skips docker/mock preflight (no agent runs); only needs .env credentials.

set -u
set -o pipefail

readonly AGENT_IMAGE="wildclawbench-ubuntu:v1.3"
readonly AGENT_IMAGE_SHA="60eec8752cb597e180780ff08d7569c1892c169521f1f2b069c2efeb006a4078"
readonly AGENT_TAR_PATH="Images/wildclawbench-ubuntu_v1.3.tar"
readonly DEFAULT_TASK="input/alden-croft_MB"
# Default model is read from .env's DEFAULT_MODEL (same convention as the other
# .env reads below), falling back to claude-opus-4.8 if unset. Avoids hardcoding
# a model that silently diverges from .env. An explicit positional model arg
# ($2) still overrides this.
DEFAULT_MODEL="$(grep -E '^DEFAULT_MODEL=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'\''' | tr -d '[:space:]')"
[[ -z "$DEFAULT_MODEL" ]] && DEFAULT_MODEL="claude-opus-4.8"
readonly DEFAULT_MODEL
readonly DEFAULT_K=1
readonly LOG_DIR="logs"
readonly HEADROOM_IMAGE="wildclawbench-litellm-headroom:v2"

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

# Best-effort installer for pv (pipe progress meter) so the 13 GB `docker load`
# is not silent for 2-15 minutes. Returns 0 if pv is already present OR after
# a successful install; returns non-zero if pv is absent and no supported
# package manager can install it without prompting. Caller MUST tolerate
# failure — the bare `docker load -i` path still works without pv.
# Never elevates privileges silently: only invokes sudo if non-root AND sudo
# can run without password (`sudo -n true`). On macOS, only uses an existing
# Homebrew; never installs Homebrew itself (that's a 2-minute interactive flow).
ensure_pv_installed() {
    if command -v pv >/dev/null 2>&1; then
        return 0
    fi
    log_warn "pv not installed; attempting auto-install for progress display"

    local sudo_cmd=""
    if [[ $EUID -ne 0 ]]; then
        if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
            sudo_cmd="sudo -n"
        fi
    fi

    case "$(uname -s)" in
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                log_info "Installing pv via Homebrew"
                if brew install pv >/dev/null 2>&1; then
                    log_ok "pv installed"
                    return 0
                fi
                log_warn "brew install pv failed"
            else
                log_warn "Homebrew not present on this macOS host; install pv manually: brew install pv"
            fi
            ;;
        Linux)
            if command -v apt-get >/dev/null 2>&1; then
                log_info "Installing pv via apt-get"
                if ${sudo_cmd} apt-get update -qq >/dev/null 2>&1 && \
                   ${sudo_cmd} apt-get install -y -qq pv >/dev/null 2>&1; then
                    log_ok "pv installed"
                    return 0
                fi
                log_warn "apt-get install pv failed (need sudo without password)"
            elif command -v dnf >/dev/null 2>&1; then
                log_info "Installing pv via dnf"
                if ${sudo_cmd} dnf install -y -q pv >/dev/null 2>&1; then
                    log_ok "pv installed"
                    return 0
                fi
                log_warn "dnf install pv failed (need sudo without password)"
            elif command -v yum >/dev/null 2>&1; then
                log_info "Installing pv via yum"
                if ${sudo_cmd} yum install -y -q pv >/dev/null 2>&1; then
                    log_ok "pv installed"
                    return 0
                fi
                log_warn "yum install pv failed (need sudo without password)"
            elif command -v apk >/dev/null 2>&1; then
                log_info "Installing pv via apk"
                if ${sudo_cmd} apk add --quiet pv >/dev/null 2>&1; then
                    log_ok "pv installed"
                    return 0
                fi
                log_warn "apk add pv failed"
            else
                log_warn "No supported package manager found (apt-get/dnf/yum/apk); install pv manually"
            fi
            ;;
        *)
            log_warn "Auto-install pv unsupported on $(uname -s); install manually if you want a progress meter"
            ;;
    esac
    return 1
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
        ensure_pv_installed || true
        if command -v pv >/dev/null 2>&1; then
            pv "$AGENT_TAR_PATH" | docker load
        else
            log_warn "pv unavailable; loading without progress display (output will be silent for several minutes)"
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

preflight_mock_image() {
    log_step "Preflight: Mock-stack image kensei3-mocks:v1"
    if docker image inspect kensei3-mocks:v1 >/dev/null 2>&1; then
        log_ok "Mock image present (no rebuild needed)"
        return 0
    fi
    log_warn "Mock image absent — building sentinel before fan-out to avoid parallel build race"
    log_info "First build takes ~3-5 min; subsequent runs skip this step"
    if ! python3 -c '
import sys
from pathlib import Path
from src.utils.mock_stack import build_mock_image_if_needed
ok = build_mock_image_if_needed(Path("environment"))
sys.exit(0 if ok else 1)
'; then
        log_err "Mock image build failed — check Docker disk space and environment/ contents"
        return 1
    fi
    log_ok "Mock image built"
}

# Agent-side Headroom prompt compression defaults ON (KENSEI_AGENT_HEADROOM_ENABLED
# unset/empty => enabled; see eval/run_batch.py). When enabled, start_litellm()
# runs the custom sidecar image HEADROOM_IMAGE (headroom-ai baked in) instead of
# the stock LiteLLM image. Nothing else builds it, so build it here if missing.
# Skip entirely when Headroom is explicitly disabled in .env.
preflight_headroom_image() {
    log_step "Preflight: Headroom LiteLLM image ${HEADROOM_IMAGE}"
    local hr_val
    hr_val=$(grep -E '^KENSEI_AGENT_HEADROOM_ENABLED=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]' | tr 'A-Z' 'a-z')
    if [[ "$hr_val" =~ ^(0|false|no|off)$ ]]; then
        log_info "Agent Headroom disabled in .env; stock LiteLLM image will be used (no build needed)"
        return 0
    fi
    if docker image inspect "$HEADROOM_IMAGE" >/dev/null 2>&1; then
        log_ok "Headroom image present (no rebuild needed)"
        return 0
    fi
    log_warn "Headroom image absent — building from docker/litellm-headroom.Dockerfile (~1-2 min)"
    if docker build -f docker/litellm-headroom.Dockerfile -t "$HEADROOM_IMAGE" . >/dev/null 2>&1; then
        log_ok "Headroom image built"
    else
        log_err "Headroom image build failed. Build it manually:"
        log_err "  docker build -f docker/litellm-headroom.Dockerfile -t ${HEADROOM_IMAGE} ."
        log_err "Or disable Headroom by setting KENSEI_AGENT_HEADROOM_ENABLED=false in .env"
        return 1
    fi
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
#
# CONCURRENCY-SAFE by default: only STOPPED containers (exited/created/dead) and
# EMPTY networks (no attached containers) are removed, so a run executing in
# parallel — in this process or a separate ./run.sh — is never touched. A
# running mock/agent container and the populated k3net-* network it sits on are
# both skipped. Pass "force" (or set WILDCLAW_FORCE_CLEAN=1) for the old nuclear
# sweep that also kills running containers — use ONLY when no other run is active.
cleanup_orphans() {
    local force="${1:-}"
    [[ "${WILDCLAW_FORCE_CLEAN:-0}" == "1" ]] && force="force"

    if [[ "$force" == "force" ]]; then
        log_step "Cleanup: FORCE removing ALL orphan containers/networks (nuclear)"
        local all_c
        all_c=$(docker ps -aq --filter 'name=ll-' --filter 'name=mocks-' --filter 'name=t_' 2>/dev/null || true)
        [[ -n "$all_c" ]] && echo "$all_c" | xargs -r docker rm -f >/dev/null 2>&1 || true
        local all_n
        all_n=$(docker network ls --filter 'name=k3net-' -q 2>/dev/null || true)
        [[ -n "$all_n" ]] && echo "$all_n" | xargs -r -n1 docker network rm >/dev/null 2>&1 || true
        log_ok "Force cleanup complete"
        return 0
    fi

    log_step "Cleanup: orphan containers and networks (stopped/empty only — live runs preserved)"

    # Only STOPPED containers are orphans. status={exited,created,dead} is AND-ed
    # with the name filters by Docker, so a 'running' mock/agent container from a
    # concurrent run is never matched.
    local containers
    containers=$(docker ps -aq \
        --filter 'status=exited' --filter 'status=created' --filter 'status=dead' \
        --filter 'name=ll-' --filter 'name=mocks-' --filter 'name=t_' 2>/dev/null || true)
    if [[ -n "$containers" ]]; then
        local count
        count=$(echo "$containers" | wc -l | tr -d ' ')
        log_warn "Found ${count} stopped orphan container(s) — removing"
        echo "$containers" | xargs -r docker rm -f >/dev/null 2>&1 || true
    else
        log_info "No stopped orphan containers"
    fi

    # Only EMPTY k3net-* networks are orphans. A network still carrying containers
    # belongs to a live run — skip it.
    local removed=0 skipped=0 net attached
    for net in $(docker network ls --filter 'name=k3net-' -q 2>/dev/null || true); do
        attached=$(docker network inspect -f '{{len .Containers}}' "$net" 2>/dev/null || echo 0)
        if [[ "$attached" == "0" ]]; then
            docker network rm "$net" >/dev/null 2>&1 && removed=$(( removed + 1 )) || true
        else
            skipped=$(( skipped + 1 ))
        fi
    done
    if (( removed > 0 || skipped > 0 )); then
        log_info "Networks: removed ${removed} empty, skipped ${skipped} in-use k3net-*"
    else
        log_info "No leaked networks"
    fi

    log_ok "Cleanup complete"
}

# Returns: stamps "$RUN_RC" and "$RUN_LOG" globals so the retry loop can inspect.
RUN_RC=0
RUN_LOG=""
# Set to "1" by the --no-subagents CLI flag; forwarded to run_batch.py so the
# model is never granted sessions_spawn (no fan-out for the whole run).
NO_SUBAGENTS=""

# Claude OAuth bridge opt-in (see bootstrap_shared_sidecar). Off by default.
# NOTE: kept EMPTY (not "0") when off so `${USE_CLAUDE_OAUTH:+--flag}` in run_one
# only expands when actually enabled. `${USE_CLAUDE_OAUTH:-0} == "1"` guards work
# with empty too.
USE_CLAUDE_OAUTH="${USE_CLAUDE_OAUTH:-}"
CC_ACCOUNT_POOL="${CC_ACCOUNT_POOL:-}"
# Shared-sidecar bookkeeping (populated by bootstrap_shared_sidecar).
WCB_SHARED_OWNER_PID=""

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
        ${NO_SUBAGENTS:+--no-subagents} \
        ${USE_CLAUDE_OAUTH:+--use-claude-oauth} \
        ${CC_ACCOUNT_POOL:+--cc-account-pool "$CC_ACCOUNT_POOL"} \
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
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
}

run_regrade() {
    local run_dir="$1"
    shift
    local rubric_override=""
    while (( $# > 0 )); do
        case "$1" in
            --rubric)
                rubric_override="${2:-}"
                if [[ -z "$rubric_override" ]]; then
                    log_err "--rubric requires a path argument"
                    return 2
                fi
                shift 2
                ;;
            *)
                log_err "Unknown --regrade option: $1"
                return 2
                ;;
        esac
    done

    if [[ ! -d "$run_dir" ]]; then
        log_err "Run directory not found: $run_dir"
        return 2
    fi

    mkdir -p "$LOG_DIR"
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    local safe_run_id
    safe_run_id=$(echo "$run_dir" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    local log_file="${LOG_DIR}/regrade_${safe_run_id}_${ts}.log"

    log_step "Regrade: $run_dir"
    log_info "Log: $log_file"
    [[ -n "$rubric_override" ]] && log_info "Rubric override: $rubric_override"

    local cmd=(python3 scripts/regrade.py --run "$run_dir")
    [[ -n "$rubric_override" ]] && cmd+=(--rubric "$rubric_override")

    "${cmd[@]}" 2>&1 | tee "$log_file"
    local rc=${PIPESTATUS[0]}

    if (( rc == 0 )); then
        log_ok "Regrade complete; score.json overwritten in $run_dir"
    else
        log_err "Regrade failed (rc=$rc); see $log_file"
    fi
    return "$rc"
}

run_k_for_model_bg() {
    local task_path="$1"
    local model="$2"
    local k="$3"
    local current_base="$4"
    local total="$5"
    local result_file="$6"

    local task_name
    task_name=$(basename "$task_path")
    local local_failed=0
    local local_recovered=0
    local local_failures=()

    for (( i=1; i<=k; i++ )); do
        local current=$(( current_base + i ))
        run_one "$task_path" "$model" "$current" "$total"

        if (( RUN_RC != 0 )); then
            if is_docker_recoverable_error "$RUN_LOG"; then
                attempt_docker_recovery
                log_step "Retry $current/$total after recovery (${model})"
                run_one "$task_path" "$model" "$current" "$total"
                if (( RUN_RC == 0 )); then
                    local_recovered=$(( local_recovered + 1 ))
                    log_ok "Run $current (${model}) succeeded after recovery"
                else
                    log_err "Run $current (${model}) failed after recovery (rc=$RUN_RC)"
                    local_failed=$(( local_failed + 1 ))
                    local_failures+=("${task_name}#${i}/${model} (rc=$RUN_RC after retry)")
                fi
            else
                log_err "Run $current (${model}) failed (rc=$RUN_RC) — no retry"
                log_err "Tail of log:"
                tail -n 20 "$RUN_LOG" >&2 || true
                local_failed=$(( local_failed + 1 ))
                local_failures+=("${task_name}#${i}/${model} (rc=$RUN_RC)")
            fi
        else
            log_ok "Run $current (${model}) completed"
        fi
    done

    {
        printf 'failed=%d\n' "$local_failed"
        printf 'recovered=%d\n' "$local_recovered"
        if (( ${#local_failures[@]} > 0 )); then
            for f in "${local_failures[@]}"; do
                printf 'failure=%s\n' "$f"
            done
        fi
    } > "$result_file"
}

# Runs every model×K for ONE task and writes a single summary file with
# `failed=`/`recovered=`/`failure=` lines. Safe to background: it forks its own
# per-model jobs and waits on them, and all results flow through files (no shared
# mutable state). Reads globals `models` and `k` (read-only).
process_task() {
    local task="$1" task_base="$2" total="$3" summary_file="$4" tmpdir="$5"
    local t_failed=0 t_recovered=0

    if [[ ! -d "$task" && ! -f "$task" ]]; then
        log_err "Task path not found: $task — skipping ($(( ${#models[@]} * k )) runs)"
        {
            printf 'failed=%d\n' "$(( ${#models[@]} * k ))"
            printf 'recovered=0\n'
            printf 'failure=%s\n' "$task (path missing)"
        } > "$summary_file"
        return 0
    fi

    log_step "Task: $(basename "$task") — fanning out to ${#models[@]} model(s)"

    local pids=() result_files=() m_idx=0 m
    for m in "${models[@]}"; do
        local m_base=$(( task_base + m_idx * k ))
        local rf="${tmpdir}/$(basename "$task")_${m//\//_}_${m_idx}.result"
        result_files+=("$rf")
        run_k_for_model_bg "$task" "$m" "$k" "$m_base" "$total" "$rf" &
        pids+=($!)
        m_idx=$(( m_idx + 1 ))
    done

    local pid
    for pid in "${pids[@]}"; do
        wait "$pid" || true
    done

    : > "$summary_file"
    local rf key value
    for rf in "${result_files[@]}"; do
        [[ -f "$rf" ]] || continue
        while IFS='=' read -r key value; do
            case "$key" in
                failed)    t_failed=$(( t_failed + value )) ;;
                recovered) t_recovered=$(( t_recovered + value )) ;;
                failure)   printf 'failure=%s\n' "$value" >> "$summary_file" ;;
            esac
        done < "$rf"
    done
    printf 'failed=%d\n' "$t_failed" >> "$summary_file"
    printf 'recovered=%d\n' "$t_recovered" >> "$summary_file"
}

# --------------------------------------------------------------------------
# Shared-infra bootstrap (Claude OAuth). Starts ONE network + LiteLLM sidecar
# (+ host-published cc-bridge) per batch via eval/bootstrap_sidecar.py, exports
# WCB_SHARED_* so every rep reuses them, auto-wires the host-side Sonnet judge
# to the OAuth bridge, and installs a ref-counted teardown trap. Ported from
# origin/claude_oauth_pathway::script/run.sh (log helpers + tmpdir chaining
# adapted to talos; `cleanup_run_marker` replaced by talos's `rm -rf $tmpdir`).
# --------------------------------------------------------------------------
bootstrap_shared_sidecar() {
    log_step "Bootstrap shared LiteLLM sidecar (one per batch, not per rep)"
    local suffix tmpfile rc=0
    suffix="$$-$(date +%Y%m%d_%H%M%S)"
    tmpfile="$(mktemp -t wcb_bootstrap.XXXXXX)"

    # python writes key=value to stdout; progress to stderr (surfaced via log_info).
    # On non-zero exit we fail loud and abort — without the sidecar no rep can
    # reach Bedrock/the bridge anyway.
    if python3 eval/bootstrap_sidecar.py --name-suffix "$suffix" > "$tmpfile" 2> >(while read -r ln; do log_info "$ln"; done); then
        rc=0
    else
        rc=$?
    fi
    if (( rc != 0 )); then
        log_err "bootstrap_sidecar.py exited rc=$rc — aborting batch"
        rm -f "$tmpfile"
        return $rc
    fi

    local k v
    while IFS='=' read -r k v; do
        case "$k" in
            name)               WCB_SHARED_SIDECAR="$v" ;;
            network)            WCB_SHARED_NETWORK="$v" ;;
            usage_log)          WCB_SHARED_SIDECAR_USAGE_LOG="$v" ;;
            yaml_path)          WCB_SHARED_SIDECAR_YAML="$v" ;;
            master_key)         WCB_SHARED_LITELLM_MASTER_KEY="$v" ;;
            cc_bridge)          WCB_SHARED_CC_BRIDGE="$v" ;;
            cc_bridge_url)      WCB_SHARED_CC_BRIDGE_URL="$v" ;;
            cc_bridge_host_url) WCB_SHARED_CC_BRIDGE_HOST_URL="$v" ;;
        esac
    done < "$tmpfile"
    rm -f "$tmpfile"

    if [[ -z "${WCB_SHARED_SIDECAR:-}" || -z "${WCB_SHARED_NETWORK:-}" ]]; then
        log_warn "Shared sidecar declined by bootstrap (no creds?); reps fall back per-process"
        return 0
    fi

    WCB_SHARED_OWNER_PID=$$
    export WCB_SHARED_NETWORK WCB_SHARED_SIDECAR \
           WCB_SHARED_SIDECAR_USAGE_LOG WCB_SHARED_SIDECAR_YAML \
           WCB_SHARED_LITELLM_MASTER_KEY WCB_SHARED_OWNER_PID \
           WCB_SHARED_CC_BRIDGE WCB_SHARED_CC_BRIDGE_URL \
           WCB_SHARED_CC_BRIDGE_HOST_URL
    log_ok "Shared sidecar=$WCB_SHARED_SIDECAR network=$WCB_SHARED_NETWORK owner_pid=$WCB_SHARED_OWNER_PID"

    # Route the HOST-side Sonnet judge through the OAuth bridge. bootstrap
    # published the in-network cc-bridge on a loopback host port; the host-side
    # grading step reaches it via 127.0.0.1. Only when OAuth is active — Bedrock
    # judging is untouched otherwise. The judge's own sonnet-only gate +
    # x-wcb-bridge-secret still apply downstream.
    if [[ -n "${WCB_SHARED_CC_BRIDGE_HOST_URL:-}" && "${USE_CLAUDE_OAUTH:-0}" == "1" ]]; then
        export KENSEI_JUDGE_OAUTH_BRIDGE_URL="$WCB_SHARED_CC_BRIDGE_HOST_URL"
        export KENSEI_JUDGE_USE_LITELLM=1
        log_ok "Sonnet judge -> OAuth bridge $WCB_SHARED_CC_BRIDGE_HOST_URL"
    fi

    # Replace the EXIT/INT/TERM traps so an interrupted batch does not leak the
    # sidecar/network/bridge. Chain talos's tmpdir cleanup (was `rm -rf $tmpdir`).
    trap 'teardown_shared_sidecar; rm -rf "${tmpdir:-}"' EXIT
    trap 'teardown_shared_sidecar; rm -rf "${tmpdir:-}"; exit 130' INT
    trap 'teardown_shared_sidecar; rm -rf "${tmpdir:-}"; exit 143' TERM
}

teardown_shared_sidecar() {
    # Owner-PID guard: under --jobs>1, workers inherit WCB_SHARED_* and each
    # worker's EXIT trap fires teardown. Without this guard the first finishing
    # worker would `docker rm -f` the SHARED sidecar out from under slower
    # siblings. Only the bootstrapping PID (owner) may tear down.
    if [[ -n "${WCB_SHARED_OWNER_PID:-}" && "$$" != "$WCB_SHARED_OWNER_PID" ]]; then
        return 0
    fi
    # Idempotent: blank the vars so a re-entrant trap (EXIT after INT) won't rm twice.
    [[ -z "${WCB_SHARED_SIDECAR:-}${WCB_SHARED_NETWORK:-}" ]] && return 0
    local sc="${WCB_SHARED_SIDECAR:-}"
    local nw="${WCB_SHARED_NETWORK:-}"
    local yp="${WCB_SHARED_SIDECAR_YAML:-}"
    local cb="${WCB_SHARED_CC_BRIDGE:-}"
    WCB_SHARED_SIDECAR=""; WCB_SHARED_NETWORK=""
    WCB_SHARED_SIDECAR_USAGE_LOG=""; WCB_SHARED_SIDECAR_YAML=""
    WCB_SHARED_LITELLM_MASTER_KEY=""
    WCB_SHARED_CC_BRIDGE=""; WCB_SHARED_CC_BRIDGE_URL=""
    WCB_SHARED_CC_BRIDGE_HOST_URL=""
    unset KENSEI_JUDGE_OAUTH_BRIDGE_URL 2>/dev/null || true
    log_info "Tearing down shared sidecar=${sc} network=${nw}${cb:+ cc_bridge=$cb}"
    [[ -n "$sc" ]] && docker rm -f "$sc" >/dev/null 2>&1 || true
    [[ -n "$cb" ]] && docker rm -f "$cb" >/dev/null 2>&1 || true
    [[ -n "$nw" ]] && docker network rm "$nw" >/dev/null 2>&1 || true
    [[ -n "$yp" && -f "$yp" ]] && rm -f "$yp" 2>/dev/null || true
}

main() {
    local mode="single"
    local tasks=()
    local models=()
    local model_arg="$DEFAULT_MODEL"
    local k="$DEFAULT_K"
    local jobs=1

    # Pre-scan for --jobs/-j and --no-subagents anywhere in the args, then strip
    # them so the existing positional/mode parsing below is unchanged.
    # Default 1 = sequential (old behavior); default subagents = task-configured.
    local _args=()
    while (( $# )); do
        case "$1" in
            --jobs|-j)           jobs="${2:-}"; shift 2 ;;
            --jobs=*)            jobs="${1#*=}"; shift ;;
            --no-subagents)      NO_SUBAGENTS=1; shift ;;
            --use-claude-oauth)  USE_CLAUDE_OAUTH=1; shift ;;
            --cc-account-pool)   CC_ACCOUNT_POOL="${2:-}"; shift 2 ;;
            --cc-account-pool=*) CC_ACCOUNT_POOL="${1#*=}"; shift ;;
            *)                   _args+=("$1"); shift ;;
        esac
    done
    set -- ${_args[@]+"${_args[@]}"}
    # Surface the OAuth opt-in to the Python side. Config.from_env reads
    # WCB_USE_CLAUDE_OAUTH / WCB_CC_ACCOUNT_POOL; bootstrap_sidecar.py + run_batch
    # pick them up. Both are inert unless --use-claude-oauth was passed.
    if [[ "${USE_CLAUDE_OAUTH:-0}" == "1" ]]; then
        export WCB_USE_CLAUDE_OAUTH=1
        [[ -n "${CC_ACCOUNT_POOL:-}" ]] && export WCB_CC_ACCOUNT_POOL="$CC_ACCOUNT_POOL"
    fi
    if ! [[ "$jobs" =~ ^[0-9]+$ ]] || (( jobs < 1 )); then
        log_err "--jobs must be a positive integer, got: $jobs"
        exit 2
    fi

    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        print_usage
        exit 0
    fi

    if [[ "${1:-}" == "--regrade" ]]; then
        shift
        local run_dir="${1:-}"
        if [[ -z "$run_dir" ]]; then
            log_err "--regrade requires a run directory argument"
            exit 2
        fi
        shift
        preflight_env_file || exit 1
        run_regrade "$run_dir" "$@"
        exit $?
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
        model_arg="${2:-$DEFAULT_MODEL}"
        k="${3:-$DEFAULT_K}"
    else
        tasks=("${1:-$DEFAULT_TASK}")
        model_arg="${2:-$DEFAULT_MODEL}"
        k="${3:-$DEFAULT_K}"
    fi

    IFS=',' read -ra models <<< "$model_arg"
    if (( ${#models[@]} == 0 )); then
        log_err "No models specified"
        exit 2
    fi

    if ! [[ "$k" =~ ^[0-9]+$ ]] || (( k < 1 )); then
        log_err "K must be a positive integer, got: $k"
        exit 2
    fi

    log_step "WildClawBench runner"
    log_info "Mode:    $mode"
    log_info "Tasks:   ${#tasks[@]} ($( (( jobs > 1 )) && echo "parallel, up to ${jobs} at a time" || echo 'sequential'))"
    log_info "Models:  ${models[*]} ($( (( ${#models[@]} > 1 )) && echo 'parallel' || echo 'single'))"
    log_info "K runs:  $k per (task,model) — sequential"
    log_info "Cwd:     $(pwd)"

    preflight_docker || exit 1
    preflight_agent_image || exit 1
    preflight_mock_image || exit 1
    preflight_headroom_image || exit 1
    preflight_env_file || exit 1
    cleanup_orphans

    local total=$(( ${#tasks[@]} * ${#models[@]} * k ))
    local failed_count=0
    local recovered_count=0
    local failed_runs=()

    local tmpdir
    tmpdir=$(mktemp -d -t wildclaw_runsh.XXXXXX)
    trap 'rm -rf "$tmpdir"' EXIT

    # Claude OAuth: bring up ONE shared network+sidecar+cc-bridge before fan-out
    # so every task/model/rep reuses it (and the host-side judge auto-wires to
    # the bridge). Must precede the concurrent task loop below. bootstrap
    # REPLACES the EXIT trap above with one that also tears the shared infra down.
    # Inert unless --use-claude-oauth was passed.
    if [[ "${USE_CLAUDE_OAUTH:-0}" == "1" ]]; then
        bootstrap_shared_sidecar || { log_err "shared sidecar bootstrap failed"; exit 1; }
    fi

    # Per-task base offset is deterministic (task_idx × models × k), so tasks need
    # no sequential accumulation and can run concurrently. Each task writes one
    # summary file; we tally them after all tasks finish.
    local per_task=$(( ${#models[@]} * k ))
    local task_idx=0
    local running=0
    local task_summaries=()
    for task in "${tasks[@]}"; do
        local task_base=$(( task_idx * per_task ))
        local sfile="${tmpdir}/task_${task_idx}.summary"
        task_summaries+=("$sfile")
        if (( jobs > 1 )); then
            process_task "$task" "$task_base" "$total" "$sfile" "$tmpdir" &
            running=$(( running + 1 ))
            if (( running >= jobs )); then
                wait -n 2>/dev/null || true
                running=$(( running - 1 ))
            fi
        else
            process_task "$task" "$task_base" "$total" "$sfile" "$tmpdir"
        fi
        task_idx=$(( task_idx + 1 ))
    done
    (( jobs > 1 )) && wait

    local sfile key value
    for sfile in "${task_summaries[@]}"; do
        [[ -f "$sfile" ]] || continue
        while IFS='=' read -r key value; do
            case "$key" in
                failed)    failed_count=$(( failed_count + value )) ;;
                recovered) recovered_count=$(( recovered_count + value )) ;;
                failure)   failed_runs+=("$value") ;;
            esac
        done < "$sfile"
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

    if (( k > 1 || ${#tasks[@]} > 1 || ${#models[@]} > 1 )); then
        log_step "Aggregating pass@K"
        if python3 scripts/aggregate_runs.py --backend openclaw 2>&1; then
            log_ok "Aggregated → output/openclaw_aggregate_summary.json"
        else
            log_warn "aggregate_runs.py failed; pass@K rollup unavailable"
        fi
    fi

    # Publish the raw run output into the Harbor "bundle" layout
    # (trajectories/<Pretty Model>/run_N/{report.json, output_media/}). Standalone
    # + non-fatal: a repackage failure never fails the run.
    if [[ -d output/openclaw ]]; then
        log_step "Harbor bundle (repackage)"
        if python3 scripts/repackage_to_bundle.py \
                --source-root output/openclaw --dest-root output_bundle --all 2>&1; then
            log_ok "Repackaged Harbor bundle → output_bundle/"
        else
            log_warn "repackage_to_bundle.py failed; published bundle not generated"
        fi
    fi

    if (( failed_count > 0 )); then
        exit 1
    fi
    log_ok "All runs completed successfully"
    exit 0
}

main "$@"
