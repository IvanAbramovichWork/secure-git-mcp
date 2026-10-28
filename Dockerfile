# Locked dependency installation and a minimal, non-root stdio runtime.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE UPSTREAM-LICENSE UPSTREAM.md ./
RUN --mount=type=secret,id=ca_bundle,mode=0444 \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --native-tls --frozen --no-dev --no-install-project --no-editable --python /usr/local/bin/python
COPY src ./src
RUN --mount=type=secret,id=ca_bundle,mode=0444 \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --native-tls --frozen --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.12-slim-bookworm
RUN --mount=type=secret,id=ca_bundle,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && ca_file=/etc/ssl/certs/ca-certificates.crt \
    && if [ -f /run/secrets/ca_bundle ]; then ca_file=/run/secrets/ca_bundle; fi \
    && apt-get -o Acquire::https::CaInfo="$ca_file" update \
    && apt-get -o Acquire::https::CaInfo="$ca_file" install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
ENTRYPOINT ["secure-git-mcp"]
