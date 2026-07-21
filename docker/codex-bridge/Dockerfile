# OpenAI Codex (ChatGPT/Codex subscription) bridge image.
#
# Sibling of docker/cc-bridge/Dockerfile. Packages the src/utils/codex_oauth
# FastAPI proxy that fronts gpt-5.6 with a ChatGPT/Codex subscription (OAuth
# from ~/.codex/auth.json, mounted at runtime) instead of a metered OpenAI key.
# Built automatically by src/utils/litellm_sidecar.py::ensure_codex_bridge_image
# (image tag wildclawbench-codex-bridge:v1). Run via the sidecar helpers; see
# docs/OPENAI_CODEX_SUBSCRIPTION.md.
FROM python:3.12-slim

RUN pip install --no-cache-dir --disable-pip-version-check \
    "fastapi==0.115.5" \
    "uvicorn==0.32.1" \
    "httpx==0.27.2" \
    "pydantic==2.10.3"

WORKDIR /app

# Packaged as the top-level module `codex_oauth` (relative imports inside the
# package make it importable both here and as src.utils.codex_oauth on the host).
COPY src/utils/codex_oauth /app/codex_oauth

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 8788

ENTRYPOINT ["python", "-m", "codex_oauth"]
CMD ["--host", "0.0.0.0", "--port", "8788"]
