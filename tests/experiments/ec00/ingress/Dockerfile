# syntax=docker/dockerfile:1
# Disposable EC-00 ingress fixture. Build from the repository root:
#   docker build -f tests/experiments/ec00/ingress/Dockerfile -t ec00-ingress-fixture:<tag> .
# Dependencies come from the candidate uv.lock (hash-verified, no resolution).
# No key material is copied: the fixture PUBLIC key is mounted at runtime and
# no private key is ever part of the build context (see Dockerfile.dockerignore).
ARG PYTHON_IMAGE=python:3.13.16-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /lock
COPY tests/experiments/ec00/candidates/pyproject.toml tests/experiments/ec00/candidates/uv.lock ./
# Runtime set only: browser automation and code generators are not installed.
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3 \
      --no-install-package playwright --no-install-package greenlet --no-install-package pyee \
      --no-install-package grpcio-tools --no-install-package protoc-gen-connectrpc \
      --no-install-package setuptools

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY packages/vuoro-service/src/vuoro_service /app/packages/vuoro-service/src/vuoro_service
COPY tests/experiments/ec00/ /app/tests/experiments/ec00/
# Build-time import smoke: the serving modules resolve against the locked set.
RUN python -c "import sys; sys.path.insert(0, '/app/tests/experiments/ec00/ingress'); sys.argv = ['x']; import serve_fixture" \
 && ! find /app /opt/venv/bin \( -name '*.pem' -o -name '*.key' \) -print | grep -q .
USER 65532:65532
EXPOSE 8080 8081 8082
ENTRYPOINT ["python", "/app/tests/experiments/ec00/ingress/serve_fixture.py"]
