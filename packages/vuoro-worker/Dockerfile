# Separate from the repository root Dockerfile deliberately: that image is
# vuoro-service's pinned-adapter composition (coordinator-owned surface,
# AGENTS.md). vuoro-worker has no domain adapters to pin -- it only needs
# itself, vuoro-service and vuoro-evidence installed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/vuoro

COPY README.md pyproject.toml uv.lock ./
COPY packages/vuoro-service ./packages/vuoro-service
COPY packages/vuoro-evidence ./packages/vuoro-evidence
COPY packages/vuoro-worker ./packages/vuoro-worker

RUN python -m pip install --no-cache-dir \
        ./packages/vuoro-evidence \
        ./packages/vuoro-service \
        ./packages/vuoro-worker \
    && python -m pip check

USER 65532:65532
# No EXPOSE: this image runs either `serve-internal-mcp` (loopback-bound by
# the deployment config that starts it, see ../../deploy/poller/) or `poll`
# (opens no listening socket at all). Neither command needs a container
# port published, and `deploy/poller/compose.yaml` does not publish one.
ENTRYPOINT ["vuoro-worker"]
CMD ["--help"]
