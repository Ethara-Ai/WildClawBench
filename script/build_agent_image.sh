#!/usr/bin/env bash
# Build one of the locally-built agent images WITHOUT script/run.sh — for hosts
# that drive `python3 eval/run_batch.py` directly, which never builds images and
# stops with "Required Docker image not present locally".
#
#   bash script/build_agent_image.sh                 # the tag in $DOCKER_IMAGE / .env, else the default v1.6
#   bash script/build_agent_image.sh v1.6            # or: wildclawbench-ubuntu:v1.6
#   bash script/build_agent_image.sh --check [tag]   # report what the tag contains, build nothing
#
# Recipes (the same ones build_agent_image() in script/run.sh uses — keep in step):
#   v1.4 = v1.3 + openai-whisper 'small'   (docker/agent-whisper.Dockerfile)   ~10–20 min
#   v1.5 = v1.3 + LibreOffice              (docker/agent-office.Dockerfile)    ~5 min
#   v1.6 = v1.4 + LibreOffice              (both layers)
# The v1.3 base is never built here: it is the HuggingFace tarball that
# script/prepare.sh / script/run.sh `docker load`.
set -u
set -o pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

readonly REPO="wildclawbench-ubuntu"
readonly BASE="${REPO}:v1.3"
readonly WHISPER="${REPO}:v1.4"
readonly OFFICE="${REPO}:v1.5"
readonly FULL="${REPO}:v1.6"

check_only=0
if [[ "${1:-}" == "--check" ]]; then
    check_only=1
    shift
fi

tag="${1:-${DOCKER_IMAGE:-}}"
if [[ -z "$tag" && -f .env ]]; then
    tag="$(sed -n 's/^[[:space:]]*DOCKER_IMAGE=//p' .env | tail -n 1 | tr -d '\r"'"'"'')"
fi
tag="${tag:-$FULL}"
[[ "$tag" == v* ]] && tag="${REPO}:${tag}"

present() { docker image inspect "$1" >/dev/null 2>&1; }

report() {
    local t="$1"
    if ! present "$t"; then
        echo "[agent-image] $t: NOT PRESENT"
        return 1
    fi
    docker run --rm --network none --entrypoint sh "$t" -c '
        printf "[agent-image] %s: soffice=%s whisper=%s weights=%s\n" "$0" \
            "$(command -v soffice >/dev/null 2>&1 && echo yes || echo no)" \
            "$(python3 -c "import whisper" >/dev/null 2>&1 && echo yes || echo no)" \
            "$([ -d /opt/wb_whisper_models ] && echo yes || echo no)"' "$t"
}

if (( check_only )); then
    report "$tag"
    exit $?
fi

if present "$tag"; then
    echo "[agent-image] $tag already present"
    report "$tag"
    exit 0
fi

if ! present "$BASE"; then
    echo "[agent-image] base $BASE is not loaded. Load it first:" >&2
    echo "  docker load -i Images/wildclawbench-ubuntu_v1.3.tar     (or: bash script/prepare.sh)" >&2
    exit 1
fi

build() {  # build <tag> <dockerfile> [base]
    local t="$1" df="$2" base="${3:-}"
    local args=(--platform linux/amd64 -f "$df" -t "$t")
    [[ -n "$base" ]] && args+=(--build-arg "BASE=$base")
    echo "[agent-image] building $t from $df${base:+ on $base}"
    docker build "${args[@]}" . || { echo "[agent-image] build of $t failed (needs internet: PyPI / apt / weight CDN)" >&2; exit 1; }
}

case "$tag" in
    "$BASE")    echo "[agent-image] $BASE is the base image; nothing to build" ;;
    "$WHISPER") build "$WHISPER" docker/agent-whisper.Dockerfile ;;
    "$OFFICE")  build "$OFFICE" docker/agent-office.Dockerfile "$BASE" ;;
    "$FULL")    present "$WHISPER" || build "$WHISPER" docker/agent-whisper.Dockerfile
                build "$FULL" docker/agent-office.Dockerfile "$WHISPER" ;;
    *)
        echo "[agent-image] no build recipe for '$tag'." >&2
        echo "  Known: $BASE (base), $WHISPER (whisper), $OFFICE (LibreOffice), $FULL (both)." >&2
        exit 2 ;;
esac

report "$tag"
