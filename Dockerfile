FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ICT_DATA_DIR=/app/data/raw \
    ICT_DATABASE_PATH=/app/data/processed/ict_agent.duckdb \
    ICT_CASE_DATABASE_PATH=/app/data/processed/ict_agent_cases.duckdb

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .

COPY --from=frontend-builder /build/frontend/dist ./frontend/dist
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x ./docker/entrypoint.sh \
    && mkdir -p /app/data/raw /app/data/processed

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["uvicorn", "ict_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
