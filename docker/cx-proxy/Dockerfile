FROM python:3.12-slim

RUN pip install --no-cache-dir --disable-pip-version-check \
    "httpx==0.27.2" \
    "cryptography==46.0.5"

WORKDIR /app

COPY src/utils/codex_oauth /app/codex_oauth

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    WCB_CX_PROXY_PORT=8770

EXPOSE 8770

ENTRYPOINT ["python", "-m", "codex_oauth"]
CMD ["--host", "0.0.0.0", "--port", "8770"]
