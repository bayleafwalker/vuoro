FROM python:3.12-slim

# The released work owner's declared-change preview parses an untrusted diff
# with `git apply --numstat` in an isolated temporary directory. The slim base
# omits Git; without it the read safely reports unavailable for every intent.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VUORO_COMPOSITION_MANIFEST=/opt/vuoro/composition/adapter-pins.json \
    VUORO_INSTALLED_COMPOSITION_PATH=/opt/vuoro/composition/installed-composition.json \
    VUORO_ADAPTER_WHEEL_DIR=/opt/vuoro/adapters

WORKDIR /srv/vuoro

COPY scripts/fetch_pinned_adapters.py /usr/local/bin/fetch-pinned-adapters
COPY scripts/attest_installed_composition.py /usr/local/bin/attest-installed-composition
COPY scripts/validate_service_image_preview.py /usr/local/bin/validate-service-image-preview
COPY packages/vuoro-service/composition/adapter-pins.json /opt/vuoro/composition/adapter-pins.json
COPY packages/vuoro-service/composition/project-bindings.json /opt/vuoro/composition/project-bindings.json
RUN python /usr/local/bin/fetch-pinned-adapters /opt/vuoro/composition/adapter-pins.json /opt/vuoro/adapters

COPY README.md pyproject.toml uv.lock ./
COPY deploy/image/requirements.txt /opt/vuoro/image-requirements.txt
COPY packages/vuoro-service ./packages/vuoro-service
COPY packages/vuoro-mcp-edge ./packages/vuoro-mcp-edge
COPY packages/vuoro-evidence ./packages/vuoro-evidence
# A build log showing the right wheels being fetched is not evidence of what
# ended up installed, so verify it and keep the result in the image.
#
# Nothing is resolved against an index. Three installs, all --no-deps:
# 1. Every third-party runtime distribution, from the hash-locked export of
#    uv.lock (deploy/image/requirements.txt, regenerated and checked by
#    scripts/export_image_requirements.py). That covers the service, the MCP
#    edge (`vuoro-service mcp-serve --port 8081`), the owners' runtime
#    dependencies (click, psycopg) and boto3 for the knowledge workspace's
#    S3-compatible object store. It also covers the released, hash-pinned
#    vuoro-client that operator-projection requires.
# 2. The workspace's local source trees: vuoro-service, vuoro-evidence,
#    vuoro-mcp-edge. Their workspace dependencies are satisfied by installs 1
#    and 3, never by an index (neither name is published; resolving them from
#    an index would install whatever someone else registered under that name).
# 3. The owner and shared wheels, from the hash-verified composition lock, as
#    in CI. The shared adapter-kit is the one lock entry, not whichever
#    release each owner's metadata names (auditctl 0.1.9 names kit 0.2.0;
#    Sprintctl 0.22.0 names kit 0.2.2). The knowledge owner
#    (vuoro-workspace 0.1.0) ships its `vuoro-workspace migrate` operator
#    entrypoint in the same wheel.
# `pip check` then proves the closure is complete. The attestation below
# verifies every installed pinned distribution.
RUN python -m pip install --no-cache-dir --require-hashes --no-deps \
        -r /opt/vuoro/image-requirements.txt \
    && python -m pip install --no-cache-dir --no-deps \
        ./packages/vuoro-service ./packages/vuoro-evidence ./packages/vuoro-mcp-edge \
    && python -m pip install --no-cache-dir --no-deps \
        /opt/vuoro/adapters/auditctl-*.whl \
        /opt/vuoro/adapters/sprintctl-*.whl \
        /opt/vuoro/adapters/vuoro_adapter_kit-*.whl \
        /opt/vuoro/adapters/vuoro_schema_runtime-*.whl \
        /opt/vuoro/adapters/vuoro_workspace-*.whl \
    && python -m pip check \
    && python /usr/local/bin/attest-installed-composition \
        /opt/vuoro/composition/adapter-pins.json \
        /opt/vuoro/adapters \
        /opt/vuoro/composition/installed-composition.json

USER 65532:65532
# Build-time qualification also runs as the service UID. A missing Git binary
# or a parser that stops redacting/refusing malformed input fails the image.
RUN python /usr/local/bin/validate-service-image-preview
EXPOSE 8080
ENTRYPOINT ["vuoro-service"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
