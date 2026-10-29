"""Central configuration. Every limit can be overridden with an SPM_* environment variable."""
from __future__ import annotations
import os, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MODEL_DIR = Path(os.environ.get("SPM_MODEL_DIR", ROOT / "model"))
SAMPLE_DIR = ROOT / "sample_data"
STATIC_DIR = ROOT / "static"
# Uploaded telemetry and results are written here. Ephemeral by design (cleaned after RUN_TTL_SECONDS).
DATA_DIR = Path(os.environ.get("SPM_DATA_DIR", Path(tempfile.gettempdir()) / "spm_runs"))
LOG_DIR = Path(os.environ.get("SPM_LOG_DIR", DATA_DIR.parent / "spm_logs"))

MAX_UPLOAD_MB = _int("SPM_MAX_UPLOAD_MB", 25)
MAX_OBSERVATIONS = _int("SPM_MAX_OBSERVATIONS", 400_000)     # telemetry rows (timestamps) scored per analysis
MAX_SHEETS = _int("SPM_MAX_SHEETS", 80)
RUN_TTL_SECONDS = _int("SPM_RUN_TTL_SECONDS", 2 * 3600)
MAX_XLSX_UNCOMPRESSED_MB = _int("SPM_MAX_XLSX_UNCOMPRESSED_MB", 400)
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}

# Points sent to the browser per panel per session. Above this the series is min/max-decimated;
# flagged readings are always kept and the full-resolution data stays server side.
DISPLAY_MAX_POINTS = _int("SPM_DISPLAY_MAX_POINTS", 150_000)

# ---- Panel triage (APPLICATION heuristic, NOT part of the supplied model, NOT validated) ----------------------------
# The model's threshold is the 99.5th percentile of out-of-session reference scores, so about 0.5 % of healthy readings
# are expected to be flagged by construction; held-out healthy data measured 0.3-0.6 % (docs/release_docs/FINAL_MODEL_CARD.md).
TRIAGE_MIN_SCORED = 30           # fewer scored readings than this -> "Insufficient data"
TRIAGE_WITHIN_EXPECTED_MAX_PCT = 2.0   # <= 2 % flagged and no persistent deviation -> "Within expected alert rate"
TRIAGE_HIGH_MIN_PCT = 10.0       # >= 10 % flagged -> "High alert rate"; in between (or any persistent run) -> "Review"

TESTED_VERSIONS = {"python": "3.12.3", "numpy": "2.4.4", "pandas": "3.0.2", "scikit-learn": "1.8.0", "scipy": "1.17.1"}
