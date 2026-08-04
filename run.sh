#!/usr/bin/env bash
# Deprecated shim: the canonical runner is script/run.sh. This root run.sh is a
# stale pre-consolidation duplicate (drifted DEFAULT_MODEL, no shared sidecar /
# OAuth path) kept only so existing `./run.sh` calls keep working. Do NOT re-add
# runner logic here; forward everything to the one canonical entry point.
exec "$(dirname "$0")/script/run.sh" "$@"
