# One container serves the API and the static frontend (main.py mounts ../frontend).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
WORKDIR /app/backend
# Cloud Run sets $PORT. No .env and no key file in the image: config comes from env vars/secrets,
# and Firebase uses the Cloud Run service account automatically.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
