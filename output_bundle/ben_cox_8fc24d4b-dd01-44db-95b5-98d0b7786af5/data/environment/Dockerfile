FROM ubuntu:24.04

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl jq python3 python3-pip ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p /root/.claude/skills
COPY skills /root/.claude/skills
RUN mkdir -p /root/.codex/skills
COPY skills /root/.codex/skills
RUN mkdir -p /root/.opencode/skills
COPY skills /root/.opencode/skills
RUN mkdir -p /root/.goose/skills
COPY skills /root/.goose/skills
RUN mkdir -p /root/.factory/skills
COPY skills /root/.factory/skills
RUN mkdir -p /root/.agents/skills
COPY skills /root/.agents/skills
RUN mkdir -p /root/.gemini/skills
COPY skills /root/.gemini/skills
RUN mkdir -p /root/.cursor/skills
COPY skills /root/.cursor/skills
