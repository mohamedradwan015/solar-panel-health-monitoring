"""Entry point.  Local:  python app.py     Production: gunicorn wsgi:app   (see README / docs/DEPLOYMENT.md)"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spm.server import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    try:
        from waitress import serve                       # production-grade server that also runs on Windows
        print(f"Solar Panel Health Monitoring: http://{host}:{port}  (waitress)")
        serve(app, host=host, port=port, threads=4, max_request_body_size=int(os.environ.get("SPM_MAX_UPLOAD_MB", "25")) * 1024 * 1024 + 8192)
    except ImportError:
        print(f"Solar Panel Health Monitoring: http://{host}:{port}  (Flask development server; install waitress for production use)")
        app.run(host=host, port=port, threaded=True)
