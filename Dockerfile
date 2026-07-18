# ---- Frontend build ---------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Runtime ------------------------------------------------------------------
FROM python:3.12-slim

# ffmpeg for audio/frame extraction; yt-dlp comes in via pip (backend dependency)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/reelarr backend/reelarr
RUN pip install --no-cache-dir ./backend

COPY --from=frontend /build/dist frontend/dist

# /config holds the SQLite db + cookie files (bind/volume mount it)
ENV DATABASE_URL=sqlite:////config/reelarr.db \
    TMP_DIR=/tmp/reelarr \
    COOKIES_DIR=/config/cookies

VOLUME /config
EXPOSE 7979

CMD ["uvicorn", "reelarr.main:app", "--host", "0.0.0.0", "--port", "7979"]
