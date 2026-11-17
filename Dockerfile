FROM node:22-alpine AS web
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv

RUN pip install --no-cache-dir uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/hlcopy ./hlcopy
RUN uv sync --frozen --no-dev

COPY --from=web /build/dist /app/static

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data

EXPOSE 8080

CMD ["/app/backend/.venv/bin/uvicorn", "hlcopy.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
