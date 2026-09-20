# docker/agent-office.Dockerfile
#
# Adds LibreOffice (headless) on top of an agent runtime image, producing
# `wildclawbench-ubuntu:v1.5`.
#
# WHY this image exists
# ─────────────────────
# `wildclawbench-ubuntu:v1.3` ships no LibreOffice, and the agent container runs
# on an --internal network, so it cannot apt-install one. Agents routinely reach
# for `soffice --headless --convert-to pdf` to render or verify a .pptx/.docx/
# .xlsx deliverable and get "timeout: failed to run command soffice: No such
# file or directory", then burn turns falling back to python-pptx. Tasks whose
# rubric depends on the RENDERED document (layout, page count, fonts) cannot be
# self-checked at all without it.
#
# Baked into the image rather than shipped through debhouse/: LibreOffice pulls
# ~150 dependency packages, which as a per-run `dpkg -i` costs minutes on every
# container start and breaks on any missing dependency.
#
# BASE is an ARG so the layer can also sit on the whisper image:
#   docker build --build-arg BASE=wildclawbench-ubuntu:v1.4 \
#       -f docker/agent-office.Dockerfile -t wildclawbench-ubuntu:v1.5 .
# v1.3 stays untouched on disk; rollback is a DOCKER_IMAGE tag flip.
#
# The proxy is unset for the build only: v1.3 bakes an unroutable corporate
# proxy into its image env (src/utils/docker_utils.py), which kills apt. No ENV
# is persisted, so the harness's runtime proxy override is unaffected.
# The final two commands are the build-time proof: soffice runs headless, and a
# real conversion succeeds (which also pre-creates the user profile so the
# agent's first call does not pay the ~20 s profile bootstrap).
ARG BASE=wildclawbench-ubuntu:v1.3
FROM ${BASE}
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY \
    && apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Proxy=false -o Acquire::https::Proxy=false \
        install -y --no-install-recommends \
        libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress \
        fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && soffice --headless --version \
    && printf 'soffice smoke test\n' > /tmp/wb_office_smoke.txt \
    && soffice --headless --convert-to pdf --outdir /tmp /tmp/wb_office_smoke.txt \
    && test -s /tmp/wb_office_smoke.pdf \
    && rm -f /tmp/wb_office_smoke.txt /tmp/wb_office_smoke.pdf
