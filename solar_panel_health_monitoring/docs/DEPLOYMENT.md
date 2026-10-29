# Deployment

A public link needs a Python host: a static page host cannot run the model. **Nothing here has been deployed or verified by the author of this project; the files below are untested configuration.** Verify after your first deploy by opening `/api/health` (it must show `"model_loaded": true`).

Requirements of any host: Python 3.11+ (3.12 tested), about 1 GB RAM recommended (350 MB peak for the 7-day sample, more for large workbooks), writable `/tmp`, request timeout of at least 120 s for big files, and the whole repository including `model/model_arrays.npz` (2.4 MB) and `cubesat_knn/`.

## Option A - Render (no Docker)
1. Put this folder in a GitHub repository (the `.gitignore` already excludes `.venv` and run data). Check the repo is private if the reference data may not be public: `model/model_arrays.npz` contains the 153,834 reference readings from BIRDS telemetry.
2. Render > New > Blueprint > choose the repo (it reads `render.yaml`), or New > Web Service with build `pip install -r requirements.txt` and start `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300`.
3. Use a plan with at least 1 GB RAM. The free 512 MB plan can be killed on large uploads.
4. The public URL is shown on the service page once the build is live.

## Option B - Hugging Face Spaces (Docker)
Create a Space with SDK *Docker*, upload the files (the `Dockerfile` listens on port 7860). Add to the top of `README.md` in the Space: `app_port: 7860` in the YAML header. Public by default; choose private if needed.

## Option C - any container host
`docker build -t spm . && docker run -p 7860:7860 spm`, then deploy the image to Cloud Run, Fly.io, Railway, etc.

## Environment variables
`SPM_MAX_UPLOAD_MB` (25), `SPM_MAX_OBSERVATIONS` (400000), `SPM_RUN_TTL_SECONDS` (7200), `SPM_DATA_DIR`, `SPM_LOG_DIR`, `SPM_MODEL_DIR`, `PORT`, `HOST`.

## Operational notes
* The model files are trusted. Do not replace `model/` with files from an unknown source. The loader verifies SHA-256 values and the pinned `MANIFEST.sha256`; when you intentionally ship a new model release, update `MANIFEST.sha256` with the new manifest's hash.
* Uploaded files are never executed or unpickled. Each upload gets a random directory; there are no user accounts, so anyone with a result link (an unguessable token) can download that run until it expires. Put the site behind authentication if the telemetry is confidential.
* Windows hosts: gunicorn does not run on Windows; `app.py` uses waitress there.
