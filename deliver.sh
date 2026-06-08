#!/usr/bin/env bash
# One-shot deliverable pipeline:  [run] -> convert -> push
#
#   (optional) 0. RUN     run the eval task(s) via ./run.sh  (Docker + API keys)
#              1. CONVERT  raw run output -> harbour CLI / "bundle" format
#                          (scripts/repackage_to_bundle.py)
#              2. BUNDLE   clone the delivery repo, drop output into test_deliverables/
#                          (folder created if absent)
#              3. PUSH     commit + push to the delivery repo's main branch
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
#   --deliverable deliverables_3   push into a different folder (default: test_deliverables)
#   (Git LFS is ON by default for large binaries .jpg/.png/.pdf/... )
#   --no-lfs                     disable Git LFS, push as plain git
#
# Non-interactive auth (EC2 / headless / CI) — no username/password prompt:
#   export GITHUB_TOKEN=ghp_xxx          # your PAT (repo scope)
#   ./deliver.sh --run --task input/ben_cox_... --task input/chris_murray_...
#
# Requires: python3, git (+ push creds for the delivery repo) and git-lfs
# (default; brew install git-lfs / apt-get install git-lfs — auto-falls back to
# plain git if absent). --run also needs Docker + a valid .env, like ./run.sh.

set -euo pipefail

# ---- configuration (override via flags) ------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_REPO="https://github.com/Ethara-Ai/kensei-delievery.git"
TARGET_BRANCH="main"
DELIVERABLE_DIR="test_deliverables"
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
        --branch)       TARGET_BRANCH="${2:?--branch needs a value}"; shift 2 ;;
        --repo)         TARGET_REPO="${2:?--repo needs a value}"; shift 2 ;;
        -h|--help)      sed -n '2,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "unknown arg: $1 (try --help)" ;;
    esac
done

command -v python3 >/dev/null || die "python3 not found in PATH"
command -v git >/dev/null     || die "git not found in PATH"
cd "$REPO_ROOT"

# ---- temp workspace (cleaned on exit) --------------------------------------
STAGING="$(mktemp -d)"
CLONE_DIR="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING" "$CLONE_DIR"; }
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

# ---- 1. convert raw output -> harbour/bundle format ------------------------
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

shopt -s nullglob dotglob
converted=("$STAGING"/*)
# Don't count the temp bulk file if it lingered.
converted=("${converted[@]/$STAGING\/.tasks.txt}")
[[ ${#converted[@]} -gt 0 ]] || die "conversion produced no bundles under staging dir"
ok "Converted ${#converted[@]} bundle(s)"

# ---- 2. clone delivery repo & stage into the deliverable folder ------------
# With a token present, fail fast instead of hanging on a prompt (headless/EC2).
[[ -n "$GH_TOKEN_VAL" ]] && export GIT_TERMINAL_PROMPT=0
info "Cloning $TARGET_REPO (branch: $TARGET_BRANCH)${GH_TOKEN_VAL:+ [token auth]}"
git clone --depth 1 --branch "$TARGET_BRANCH" "$(_auth_url "$TARGET_REPO")" "$CLONE_DIR" \
    || die "clone failed (check access/credentials and that branch '$TARGET_BRANCH' exists)"

cd "$CLONE_DIR"

# Optional: route large binaries through Git LFS so git history stays lean.
if [[ "$USE_LFS" -eq 1 ]] && ! command -v git-lfs >/dev/null; then
    if [[ "$LFS_EXPLICIT" -eq 1 ]]; then
        die "git-lfs not installed (macOS: brew install git-lfs ; Ubuntu/EC2: sudo apt-get install -y git-lfs)"
    fi
    warn "git-lfs not installed — falling back to plain git (install git-lfs to enable LFS, or pass --no-lfs to silence)"
    USE_LFS=0
fi
if [[ "$USE_LFS" -eq 1 ]]; then
    info "Enabling Git LFS for large binary artifacts"
    git lfs install --local >/dev/null
    git lfs track "*.jpg" "*.jpeg" "*.png" "*.gif" "*.webp" "*.pdf" "*.zip" \
                  "*.m4a" "*.mp3" "*.wav" "*.mp4" "*.mov" "*.docx" "*.xlsx" "*.pptx" >/dev/null
    git add .gitattributes
fi

DEST="$CLONE_DIR/$DELIVERABLE_DIR"
mkdir -p "$DEST"            # create folder if it doesn't exist
info "Copying converted bundles into $DELIVERABLE_DIR/"
rm -f "$STAGING/.tasks.txt" 2>/dev/null || true
cp -R "$STAGING"/. "$DEST"/

# ---- 3. commit + push ------------------------------------------------------
git add "$DELIVERABLE_DIR"
if git diff --cached --quiet; then
    warn "No changes to commit (delivery repo already up to date). Nothing to push."
    exit 0
fi

STAMP="$(date -u '+%Y-%m-%d %H:%M:%SZ')"
git commit -m "Add ${DELIVERABLE_DIR} (harbour CLI bundles, ${#converted[@]} item(s)) — ${STAMP}"

if [[ "$DRY_RUN" -eq 1 ]]; then
    warn "--dry-run set: committed locally in the clone but NOT pushing."
    info "Re-run without --dry-run to push to '$TARGET_BRANCH'."
    exit 0
fi

info "Pushing to $TARGET_REPO ($TARGET_BRANCH)"
git push origin "$TARGET_BRANCH" || die "push failed (check push access to the delivery repo)"
ok "Pushed ${DELIVERABLE_DIR}/ to $TARGET_BRANCH"
