# Container image for Render, Hugging Face Spaces (Docker SDK), Railway, Fly.io, Cloud Run, etc.
# NOTE: this Dockerfile was written but NOT built in the development sandbox (no Docker, no network).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SPM_DATA_DIR=/tmp/spm_runs SPM_LOG_DIR=/tmp/spm_logs
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 7860
# One worker keeps memory low (the model plus pandas peaks near 350 MB); threads handle concurrent requests.
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 4 --timeout 300 --max-requests 200"]
