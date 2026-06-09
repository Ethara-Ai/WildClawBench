#!/usr/bin/env bash
# One-shot deliverable pipeline:  [run] -> push RAW -> convert -> push CONVERTED
#
#   (optional) 0. RUN      run the eval task(s) via ./run.sh  (Docker + API keys)
#              1. PUSH RAW  push the raw run-output to the DATASETS repo
#                           (kensei-datasets), before any conversion
#              2. CONVERT   raw run output -> harbour CLI / "bundle" format
#                           (scripts/repackage_to_bundle.py)
#              3. PUSH      push converted bundles to the DELIVERY repo
#                           (kensei-delievery)
#
# BOTH repos get the same layout:  <deliverable>/<YYYY-MM-DD>/<task dirs>/
# All tasks delivered on a given day land in that day's date folder; if the
# folder already exists in the repo it is reused (not recreated). Concurrent
# task deliveries are conflict-safe: each push rebases on top of any push that
# landed first and retries, so two tasks finishing together don't clobber.
#
# Two modes:
#   CONVERT-ONLY (default): packages whatever already exists under output/.
#   RUN (--run): runs the eval first to produce FRESH output, then packages it.
#
# Usage:
#   # convert existing output and push (no eval run):
#   ./deliver.sh                                  # all existing bundles
#   ./deliver.sh --persona "amanda hayes"         # one existing bundle
#
#   # run eval first, then convert + push (uses run.sh defaults: claude-opus-4.7, K=1):
#   ./deliver.sh --run --task input/amanda_hayes_01                 # single task
#   ./deliver.sh --run --task input/amanda_hayes_01 --task input/chris_event   # several
#   ./deliver.sh --run --tasks-file my_tasks.txt                    # a list (one path/line)
#   ./deliver.sh --run --all-tasks                                  # every task under input/
#   ./deliver.sh --run --task input/chris_event --model claude-opus-4.7 -k 3   # override
#
#   --dry-run                    everything EXCEPT the final push (safe test)
#   --date 2026-06-09            override the date folder (default: today, local)
#   --no-raw                     skip the raw datasets push (converted only)
#   --datasets-repo <url>        override the raw datasets repo
#   --deliverable other_dir      push into a different top folder (default: delivery)
#   (Git LFS is ON by default for large binaries .jpg/.png/.pdf/... )
#   --no-lfs                     disable Git LFS, push as plain git
#
# Non-interactive auth (EC2 / headless / CI) — no username/password prompt:
#   export GITHUB_TOKEN=ghp_xxx          # your PAT (repo scope; needs access to BOTH repos)
#   ./deliver.sh --run --task input/ben_cox_... --task input/chris_murray_...
#
# Requires: python3, git (+ push creds for BOTH the datasets and delivery repos)
# and git-lfs (default; brew install git-lfs / apt-get install git-lfs —
# auto-falls back to plain git if absent). --run also needs Docker + valid .env.

set -euo pipefail

# ---- configuration (override via flags) ------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_REPO="https://github.com/Ethara-Ai/kensei-delievery.git"   # CONVERTED bundles
DATASETS_REPO="https://github.com/Ethara-Ai/kensei-datasets.git"  # RAW run output
TARGET_BRANCH="main"
DELIVERABLE_DIR="delivery"
RUN_DATE="$(date +%F)"            # per-run date folder (YYYY-MM-DD); override with --date
DO_RAW=1                          # push raw output to datasets repo (--no-raw to skip)
SOURCE_ROOT="output/openclaw"     # raw run-output tree to convert
INPUT_ROOT="input"                # where task dirs live (for --all-tasks)
PERSONA=""                        # convert-only: one persona (empty => --all)
DRY_RUN=0

DO_RUN=0                          # --run: run the eval before converting
ALL_TASKS=0                       # --all-tasks: run every task under input/
TASKS_FILE=""                     # --tasks-file: bulk list
MODEL=""                          # --model: passthrough to run.sh (empty => its default)
K_RUNS=""                         # -k: passthrough to run.sh (empty => its default)
declare -a RUN_TASKS=()           # --task (repeatable)

USE_LFS=1                         # default ON: track large binaries via git-lfs (--no-lfs to disable)
LFS_EXPLICIT=0                    # set when --lfs is passed explicitly (then a missing git-lfs is fatal)
LFS_GLOBS=("*.jpg" "*.jpeg" "*.png" "*.gif" "*.webp" "*.pdf" "*.zip" \
           "*.m4a" "*.mp3" "*.wav" "*.mp4" "*.mov" "*.docx" "*.xlsx" "*.pptx")
# Non-interactive auth (EC2/headless): export GITHUB_TOKEN or GH_TOKEN before running.
GH_TOKEN_VAL="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

# ---- pretty logging --------------------------------------------------------
if [[ -t 1 ]]; then
    C_B=$'\033[0;34m'; C_G=$'\033[0;32m'; C_Y=$'\033[0;33m'; C_R=$'\033[0;31m'; C_X=$'\033[0m'
else
    C_B=''; C_G=''; C_Y=''; C_R=''; C_X=''
fi
info(){ printf '%s[INFO]%s %s\n' "$C_B" "$C_X" "$*"; }
ok(){   printf '%s[OK]%s   %s\n' "$C_G" "$C_X" "$*"; }
warn(){ printf '%s[WARN]%s %s\n' "$C_Y" "$C_X" "$*" >&2; }
err(){  printf '%s[ERR]%s  %s\n' "$C_R" "$C_X" "$*" >&2; }
die(){  err "$*"; exit 1; }

# Inject a token into an https URL for non-interactive clone/push (EC2/headless).
_auth_url(){
    local url="$1"
    if [[ -n "$GH_TOKEN_VAL" && "$url" == https://* ]]; then
        printf 'https://x-access-token:%s@%s' "$GH_TOKEN_VAL" "${url#https://}"
    else
        printf '%s' "$url"
    fi
}

# Resolve which raw run-output dirs under SOURCE_ROOT match a persona selector.
# Arg1: a persona string, or the literal "__ALL__" to take every task dir.
# Prints one absolute dir path per line. Reuses repackage_to_bundle.persona_core
# so the raw selection matches exactly what the convert step delivers.
_raw_dirs_for_persona(){
    python3 - "$REPO_ROOT/$SOURCE_ROOT" "$1" <<'PY'
import sys, pathlib
sys.path.insert(0, "scripts")
from repackage_to_bundle import persona_core
root = pathlib.Path(sys.argv[1]); sel = sys.argv[2]
want = None if sel == "__ALL__" else persona_core(sel)
for p in sorted(root.iterdir()):
    if not p.is_dir():
        continue
    core = persona_core(p.name)
    if want is None or core == want or (want and want in core):
        print(p)
PY
}

# Conflict-safe publish of a staged tree into <deliverable>/<date>/ of a repo.
#   publish_to_repo <repo_url> <branch> <deliverable_dir> <run_date> <src_dir> <label> <count>
# Clones fresh, (optionally) enables Git LFS, copies $src_dir/. into the dated
# folder (reusing it if it already exists), commits, and pushes. On a rejected
# push (a concurrent task moved the branch) it rebases on top and retries, so
# parallel task deliveries don't clobber each other.
publish_to_repo(){
    local repo_url="$1" branch="$2" deliv="$3" rundate="$4" srcdir="$5" label="$6" count="$7"
    local clone; clone="$(mktemp -d)"

    info "[$label] cloning $repo_url (branch: $branch)${GH_TOKEN_VAL:+ [token auth]}"
    if ! git clone --depth 1 --branch "$branch" "$(_auth_url "$repo_url")" "$clone" 2>/dev/null; then
        warn "[$label] branch '$branch' not present — cloning default and creating it"
        git clone --depth 1 "$(_auth_url "$repo_url")" "$clone" \
            || { rm -rf "$clone"; die "[$label] clone failed (check access to $repo_url)"; }
        git -C "$clone" checkout -B "$branch"
    fi

    (
        cd "$clone"
        if [[ "$USE_LFS" -eq 1 ]]; then
            git lfs install --local >/dev/null 2>&1 || true
            git lfs track "${LFS_GLOBS[@]}" >/dev/null 2>&1 || true
            git add .gitattributes 2>/dev/null || true
        fi

        local dest="$clone/$deliv/$rundate"
        mkdir -p "$dest"                       # reuse today's folder if it already exists
        info "[$label] copying $count item(s) into $deliv/$rundate/"
        cp -R "$srcdir"/. "$dest"/

        # Raw run-output can contain an agent's own workspace as a nested git repo
        # (.../workspace_full/.git). Left in place, `git add` aborts with "does not
        # have a commit checked out" and the whole push silently stages nothing.
        # Strip every embedded .git so we commit plain files, not a repo-in-repo.
        find "$dest" -depth -name .git -exec rm -rf {} + 2>/dev/null || true

        git add "$deliv"
        if git diff --cached --quiet; then
            warn "[$label] no changes to commit — already up to date."
            exit 0
        fi
        local stamp; stamp="$(date -u '+%Y-%m-%d %H:%M:%SZ')"
        git commit -q -m "$label $rundate ($count item(s)) — $stamp"

        if [[ "$DRY_RUN" -eq 1 ]]; then
            warn "[$label] --dry-run: committed locally in clone but NOT pushing."
            exit 0
        fi

        # Push with rebase-on-conflict retry (safe for concurrent task deliveries).
        local tries=0 max=6
        until git push origin "$branch"; do
            tries=$((tries + 1))
            if (( tries > max )); then
                err "[$label] push still failing after $max retries — giving up."
                exit 1
            fi
            warn "[$label] push rejected (remote moved) — rebasing & retrying ($tries/$max)"
            git fetch --depth 50 origin "$branch" 2>/dev/null || git fetch origin "$branch" || true
            git rebase "origin/$branch" || git pull --rebase --no-edit origin "$branch" || true
            sleep 2
        done
        ok "[$label] pushed $deliv/$rundate/ to $branch"
    )
    local rc=$?
    rm -rf "$clone"
    return $rc
}

# ---- args ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run)          DO_RUN=1; shift ;;
        --lfs)          USE_LFS=1; LFS_EXPLICIT=1; shift ;;
        --no-lfs)       USE_LFS=0; shift ;;
        --task)         RUN_TASKS+=("${2:?--task needs a path}"); shift 2 ;;
        --tasks-file)   TASKS_FILE="${2:?--tasks-file needs a path}"; shift 2 ;;
        --all-tasks)    ALL_TASKS=1; shift ;;
        --model)        MODEL="${2:?--model needs a value}"; shift 2 ;;
        -k)             K_RUNS="${2:?-k needs a value}"; shift 2 ;;
        --dry-run)      DRY_RUN=1; shift ;;
        --persona)      PERSONA="${2:?--persona needs a value}"; shift 2 ;;
        --source-root)  SOURCE_ROOT="${2:?--source-root needs a value}"; shift 2 ;;
        --deliverable)  DELIVERABLE_DIR="${2:?--deliverable needs a value}"; shift 2 ;;
        --date)         RUN_DATE="${2:?--date needs a value (YYYY-MM-DD)}"; shift 2 ;;
        --no-raw)       DO_RAW=0; shift ;;
        --datasets-repo) DATASETS_REPO="${2:?--datasets-repo needs a value}"; shift 2 ;;
        --branch)       TARGET_BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --repo)         TARGET_REPO="${2:?--repo needs a value}"; shift 2 ;;
        -h|--help)      sed -n '2,48p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "unknown arg: $1 (try --help)" ;;
    esac
done

command -v python3 >/dev/null || die "python3 not found in PATH"
command -v git >/dev/null     || die "git not found in PATH"
cd "$REPO_ROOT"

# ---- temp workspace (cleaned on exit) --------------------------------------
STAGING="$(mktemp -d)"          # converted bundles
RAW_STAGING="$(mktemp -d)"      # raw run-output, selected per persona
cleanup(){ rm -rf "$STAGING" "$RAW_STAGING"; }
trap cleanup EXIT

# ---- 0. (optional) RUN the eval to produce fresh output --------------------
# Collects the task list, invokes ./run.sh, and records which persona bundles
# to convert afterwards (so we publish exactly what we just ran).
declare -a CONVERT_PERSONAS=()    # personas to convert after a run

if [[ "$DO_RUN" -eq 1 ]]; then
    [[ -x "$REPO_ROOT/run.sh" ]] || die "run.sh not found/executable in $REPO_ROOT"

    # Build the effective task list from --task / --tasks-file / --all-tasks.
    declare -a TASKS=()
    if [[ "$ALL_TASKS" -eq 1 ]]; then
        shopt -s nullglob
        for d in "$INPUT_ROOT"/*/; do TASKS+=("${d%/}"); done
        shopt -u nullglob
    fi
    if [[ -n "$TASKS_FILE" ]]; then
        [[ -f "$TASKS_FILE" ]] || die "--tasks-file not found: $TASKS_FILE"
        while IFS= read -r line; do
            line="${line%%#*}"; line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
            [[ -n "$line" ]] && TASKS+=("$line")
        done < "$TASKS_FILE"
    fi
    TASKS+=("${RUN_TASKS[@]}")

    [[ ${#TASKS[@]} -gt 0 ]] || die "--run needs tasks: use --task <path> (repeatable), --tasks-file <file>, or --all-tasks"

    # run.sh single mode takes ONE task; for >1 we hand it a temp --bulk file.
    info "Running eval for ${#TASKS[@]} task(s) via run.sh  (model: ${MODEL:-default}, K: ${K_RUNS:-default})"
    if [[ ${#TASKS[@]} -eq 1 ]]; then
        RUN_ARGS=("${TASKS[0]}")
        [[ -n "$MODEL" ]] && RUN_ARGS+=("$MODEL")
        # run.sh K is positional after model; if -k set without --model, fall back to its default model.
        if [[ -n "$K_RUNS" ]]; then
            [[ -n "$MODEL" ]] || RUN_ARGS+=("claude-opus-4.7")
            RUN_ARGS+=("$K_RUNS")
        fi
        "$REPO_ROOT/run.sh" "${RUN_ARGS[@]}" || die "run.sh failed for ${TASKS[0]}"
    else
        BULK_FILE="$STAGING/.tasks.txt"
        printf '%s\n' "${TASKS[@]}" > "$BULK_FILE"
        RUN_ARGS=(--bulk "$BULK_FILE")
        [[ -n "$MODEL" ]] && RUN_ARGS+=("$MODEL")
        if [[ -n "$K_RUNS" ]]; then
            [[ -n "$MODEL" ]] || RUN_ARGS+=("claude-opus-4.7")
            RUN_ARGS+=("$K_RUNS")
        fi
        "$REPO_ROOT/run.sh" "${RUN_ARGS[@]}" || die "run.sh --bulk failed"
        rm -f "$BULK_FILE"
    fi
    ok "Eval run(s) complete"

    # Convert exactly the tasks we just ran (basename -> fuzzy persona match).
    for t in "${TASKS[@]}"; do CONVERT_PERSONAS+=("$(basename "$t")"); done
fi

[[ -d "$REPO_ROOT/$SOURCE_ROOT" ]] || die "source root not found: $SOURCE_ROOT (nothing to convert)"

# With a token present, fail fast instead of hanging on a prompt (headless/EC2).
[[ -n "$GH_TOKEN_VAL" ]] && export GIT_TERMINAL_PROMPT=0

# Resolve Git LFS availability once; publish_to_repo honors the final USE_LFS.
if [[ "$USE_LFS" -eq 1 ]] && ! command -v git-lfs >/dev/null; then
    if [[ "$LFS_EXPLICIT" -eq 1 ]]; then
        die "git-lfs not installed (macOS: brew install git-lfs ; Ubuntu/EC2: sudo apt-get install -y git-lfs)"
    fi
    warn "git-lfs not installed — falling back to plain git (install git-lfs to enable LFS, or pass --no-lfs to silence)"
    USE_LFS=0
fi

# Persona selectors for this delivery — the exact set the convert step will use
# (run-mode personas, else one --persona, else every task = __ALL__).
declare -a SELECTORS=()
if [[ ${#CONVERT_PERSONAS[@]} -gt 0 ]]; then SELECTORS=("${CONVERT_PERSONAS[@]}")
elif [[ -n "$PERSONA" ]];            then SELECTORS=("$PERSONA")
else                                      SELECTORS=("__ALL__")
fi

# ---- 1. push RAW run-output -> datasets repo (before converting) -----------
if [[ "$DO_RAW" -eq 1 ]]; then
    info "Staging raw run-output (source: $SOURCE_ROOT) for datasets push"
    raw_n=0
    for sel in "${SELECTORS[@]}"; do
        while IFS= read -r d; do
            [[ -n "$d" ]] || continue
            cp -R "$d" "$RAW_STAGING/" && raw_n=$((raw_n + 1))
        done < <(_raw_dirs_for_persona "$sel")
    done
    if [[ "$raw_n" -gt 0 ]]; then
        ok "Staged $raw_n raw task dir(s)"
        publish_to_repo "$DATASETS_REPO" "$TARGET_BRANCH" "$DELIVERABLE_DIR" "$RUN_DATE" \
            "$RAW_STAGING" "raw-dataset" "$raw_n" || die "raw dataset push failed"
    else
        warn "No raw task dirs matched the selection — skipping datasets push."
    fi
else
    info "--no-raw set: skipping raw datasets push."
fi

# ---- 2. convert raw output -> harbour/bundle format ------------------------
info "Converting output -> harbour CLI format (source: $SOURCE_ROOT)"
if [[ "${#CONVERT_PERSONAS[@]}" -gt 0 ]]; then
    # Run mode: convert each freshly-run task individually by persona.
    for p in "${CONVERT_PERSONAS[@]}"; do
        info "  convert persona: $p"
        python3 "$REPO_ROOT/scripts/repackage_to_bundle.py" \
            --source-root "$SOURCE_ROOT" --dest-root "$STAGING" --persona "$p" \
            || die "conversion failed for persona '$p'"
    done
else
    # Convert-only mode: --persona (one) or --all.
    REPACKAGE_ARGS=(--source-root "$SOURCE_ROOT" --dest-root "$STAGING")
    if [[ -n "$PERSONA" ]]; then REPACKAGE_ARGS+=(--persona "$PERSONA"); else REPACKAGE_ARGS+=(--all); fi
    python3 "$REPO_ROOT/scripts/repackage_to_bundle.py" "${REPACKAGE_ARGS[@]}"
fi

rm -f "$STAGING/.tasks.txt" 2>/dev/null || true   # drop temp bulk file if it lingered
shopt -s nullglob dotglob
converted=("$STAGING"/*)
[[ ${#converted[@]} -gt 0 ]] || die "conversion produced no bundles under staging dir"
ok "Converted ${#converted[@]} bundle(s)"

# ---- 3. push CONVERTED bundles -> delivery repo ----------------------------
publish_to_repo "$TARGET_REPO" "$TARGET_BRANCH" "$DELIVERABLE_DIR" "$RUN_DATE" \
    "$STAGING" "delivery-bundle" "${#converted[@]}" || die "delivery push failed"
