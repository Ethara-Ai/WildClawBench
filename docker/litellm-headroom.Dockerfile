# docker/litellm-headroom.Dockerfile
#
# Custom LiteLLM sidecar image that bakes in `headroom-ai` so the
# `litellm_headroom_callback.HeadroomPreCallCompressor` callback can `import
# headroom` at proxy startup time. The stock `ghcr.io/berriai/litellm:main-stable`
# image does NOT ship headroom and the sidecar has no internet egress at the
# point the callback is loaded (only the dual-homed `bridge` NIC is added AFTER
# health probe, see `src/utils/litellm_sidecar.py::connect_default_bridge`), so
# baking it into the image is the only reliable strategy.
#
# WHY a separate image (not pip-install at startup):
#   - `LITELLM_IMAGE = "ghcr.io/berriai/litellm:main-stable"` is the contract for
#     today's production sidecar (`src/utils/litellm_sidecar.py:11`). Modifying
#     entry-point pip-install introduces a startup race against
#     `_init_litellm_callbacks` which resolves callback strings synchronously
#     before the health probe (`wait_for_litellm_healthy`). A baked image
#     guarantees `import headroom` succeeds on first byte of proxy startup.
#   - Network policy: agent containers are on an `--internal` bridge with NO
#     internet egress (RUNBOOK.md §10 invariant #1 "No internet egress from the
#     agent."). The sidecar IS dual-homed to `bridge` but only AFTER
#     `connect_default_bridge` runs post-start. A pip-install at entrypoint
#     therefore can't reliably fetch headroom-ai from PyPI before LiteLLM tries
#     to resolve the callback symbol.
#
# WHY this image is only used when KENSEI_AGENT_HEADROOM_ENABLED=true:
#   - Default OFF: sidecar runs identical to today (uses
#     `ghcr.io/berriai/litellm:main-stable` unchanged). The custom image is only
#     selected by `start_litellm()` when the headroom callback is enabled — this
#     preserves the existing production hot path exactly as it is today.
#
# Pin: headroom-ai>=0.24,<0.25 matches the host-side judge pin in
# `requirements.txt:13` (committed in ad670b8). Keeping the version range
# identical guarantees `compress()` behavior parity between the host-side judge
# path and the in-sidecar agent path, so the same CompressConfig knobs map to
# the same compression semantics on both sides.
#
# BUILD:
#   docker build -f docker/litellm-headroom.Dockerfile -t wildclawbench-litellm-headroom:v1 .
# The build context is the repo root; nothing from the repo is COPYed in (the
# callback file is bind-mounted at runtime by `start_litellm` like
# `litellm_usage_callback.py` is today — see
# `src/utils/litellm_sidecar.py:353`), so this Dockerfile is intentionally
# context-independent and reproducible from any checkout.
FROM ghcr.io/berriai/litellm:main-stable

# `--no-cache-dir` keeps the layer small; the wheel is small (~1MB) so caching
# buys nothing and costs disk on every layer rebuild.
# We intentionally do NOT pin a sub-patch — any 0.24.x is acceptable as long as
# it's compatible with the host-side judge module's pin.
#
# The stock LiteLLM image uses a uv-managed venv at /app/.venv with no pip
# pre-installed and uv stripped from PATH (verified empirically: bare `pip`
# exits 127; `python -m pip` exits 1 "No module named pip"; bare `uv` exits 127).
# Bootstrap pip into the venv via the stdlib `ensurepip` module — this is
# Python-version-independent and doesn't require any external tooling on PATH.
RUN python -m ensurepip --upgrade \
    && python -m pip install --no-cache-dir 'headroom-ai>=0.24,<0.25'

# No CMD/ENTRYPOINT override — inherits LiteLLM's stock entrypoint
# (`litellm --config /app/config.yaml --port <port>`), which is what
# `start_litellm()` constructs at runtime.
