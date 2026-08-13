FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VUORO_COMPOSITION_MANIFEST=/opt/vuoro/composition/adapter-pins.json \
    VUORO_INSTALLED_COMPOSITION_PATH=/opt/vuoro/composition/installed-composition.json \
    VUORO_ADAPTER_WHEEL_DIR=/opt/vuoro/adapters

WORKDIR /srv/vuoro

COPY scripts/fetch_pinned_adapters.py /usr/local/bin/fetch-pinned-adapters
COPY scripts/attest_installed_composition.py /usr/local/bin/attest-installed-composition
COPY packages/vuoro-service/composition/adapter-pins.json /opt/vuoro/composition/adapter-pins.json
COPY packages/vuoro-service/composition/project-bindings.json /opt/vuoro/composition/project-bindings.json
RUN python /usr/local/bin/fetch-pinned-adapters /opt/vuoro/composition/adapter-pins.json /opt/vuoro/adapters

COPY README.md pyproject.toml uv.lock ./
COPY packages/vuoro-service ./packages/vuoro-service
# A build log showing the right wheels being fetched is not evidence of what
# ended up installed, so verify it and keep the result in the image.
RUN python -m pip install --no-cache-dir "psycopg[binary]>=3.2,<4" "click>=8.1" ./packages/vuoro-service \
    && python -m pip install --no-cache-dir \
        /opt/vuoro/adapters/actionq_contracts-*.whl \
        /opt/vuoro/adapters/actionq-*.whl \
        /opt/vuoro/adapters/auditctl-*.whl \
        /opt/vuoro/adapters/kctl-*.whl \
        /opt/vuoro/adapters/sprintctl-*.whl \
    && python -m pip install --no-cache-dir --no-deps /opt/vuoro/adapters/vuoro_adapter_kit-*.whl \
    && python -m pip check \
    && python /usr/local/bin/attest-installed-composition \
        /opt/vuoro/composition/adapter-pins.json \
        /opt/vuoro/adapters \
        /opt/vuoro/composition/installed-composition.json

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["vuoro-service"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
