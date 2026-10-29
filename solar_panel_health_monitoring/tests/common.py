import os, sys, gzip, shutil, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
_tmp = tempfile.mkdtemp(prefix="spm_test_")
os.environ["SPM_DATA_DIR"] = str(Path(_tmp) / "runs"); os.environ["SPM_LOG_DIR"] = str(Path(_tmp) / "logs")
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"
import pandas as pd, numpy as np

_syn = None
def syn7_csv(dest_dir: Path) -> Path:
    """Decompress the synthetic 7-day telemetry fixture once per test session."""
    global _syn
    p = Path(_tmp) / "syn7_holdout_telemetry.csv"
    if not p.exists():
        with gzip.open(FIX / "syn7_holdout_telemetry.csv.gz", "rb") as f, open(p, "wb") as o: shutil.copyfileobj(f, o)
    return p

def make_flat_csv(path: Path, n=600, panels=("P1", "P2"), seed=0, v_unit="V", i_unit="A", scale_i=1.0, with_iso=False):
    """Small synthetic flat file drawn from the real reference range (NOT real telemetry)."""
    rs = np.random.RandomState(seed); t = np.arange(n) * 10.0
    d = {"elapsed_seconds": t}
    if with_iso: d = {"timestamp_utc": pd.date_range("2026-01-01", periods=n, freq="10s").strftime("%Y-%m-%dT%H:%M:%SZ"), **d}
    for p in panels:
        v = 4.8 + 0.4 * np.sin(t / 300 + hash(p) % 5) + rs.normal(0, 0.05, n)       # volts
        i = np.clip(160 + 130 * np.sin(t / 300 + hash(p) % 5) + rs.normal(0, 5, n), 0, None) * scale_i  # mA, p99 close to the reference scale
        d[f"{p}_voltage_{v_unit}"] = v if v_unit == "V" else v * 1000
        d[f"{p}_current_{i_unit}"] = i / 1000 if i_unit == "A" else i
    pd.DataFrame(d).to_csv(path, index=False); return path
