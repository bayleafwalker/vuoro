FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VUORO_COMPOSITION_MANIFEST=/opt/vuoro/composition/adapter-pins.json \
    VUORO_ADAPTER_WHEEL_DIR=/opt/vuoro/adapters

WORKDIR /srv/vuoro

COPY scripts/fetch_pinned_adapters.py /usr/local/bin/fetch-pinned-adapters
COPY packages/vuoro-service/composition/adapter-pins.json /opt/vuoro/composition/adapter-pins.json
RUN python /usr/local/bin/fetch-pinned-adapters /opt/vuoro/composition/adapter-pins.json /opt/vuoro/adapters

COPY README.md pyproject.toml uv.lock ./
COPY packages/vuoro-service ./packages/vuoro-service
RUN python -m pip install --no-cache-dir "psycopg[binary]>=3.2,<4" ./packages/vuoro-service /opt/vuoro/adapters/*.whl

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["vuoro-service"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
