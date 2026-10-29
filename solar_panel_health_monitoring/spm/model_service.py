"""Loads the supplied model artifacts once (integrity-checked) and exposes inference. No model logic lives here."""
from __future__ import annotations
import json, platform, threading
from pathlib import Path
import numpy as np, pandas as pd, scipy, sklearn
from cubesat_knn.pipeline import CubeSatKNNPipeline, IncompatibleBaselineError
from cubesat_knn.features import FeatureSchemaError, build_channel_features
from cubesat_knn.schema_detector import SchemaError
from . import settings

__all__ = ["ModelService", "ModelLoadError", "IncompatibleBaselineError", "FeatureSchemaError", "SchemaError"]


class ModelLoadError(RuntimeError):
    pass


class ModelService:
    """Wraps CubeSatKNNPipeline.load_trusted_artifacts (SHA-256 verified against manifest.json, no pickle)."""

    def __init__(self, model_dir: Path | str = settings.MODEL_DIR):
        self.model_dir = Path(model_dir)
        self._lock = threading.Lock()          # scoring is CPU bound and uses shared sklearn state; serialise it
        try:
            pin = self.model_dir / "MANIFEST.sha256"
            expected = pin.read_text().strip() if pin.exists() else None
            self.pipe = CubeSatKNNPipeline.load_trusted_artifacts(self.model_dir, expected_manifest_sha256=expected)
            self.manifest = json.loads((self.model_dir / "manifest.json").read_text())
        except FileNotFoundError as e:
            raise ModelLoadError(f"Model artifact missing: {e.filename}. Expected the files listed in model/manifest.json.") from e
        except Exception as e:                  # integrity failure, bad JSON, bad npz ...
            raise ModelLoadError(f"Model artifacts could not be loaded safely: {e}") from e
        cfg = self.pipe.cfg
        if cfg.level != "channel":
            raise ModelLoadError("This application expects a channel-level (per-panel) model; the loaded model is "
                                 f"level={cfg.level!r}.")
        self.version_warnings = self._version_warnings()

    # ------------------------------------------------------------------ information
    def _version_warnings(self) -> list[str]:
        built = self.manifest.get("software", {})
        now = {"numpy": np.__version__, "pandas": pd.__version__, "scikit-learn": sklearn.__version__, "scipy": scipy.__version__}
        return [f"{k} {now[k]} is installed; the model was built and tested with {built[k]}. Results should match, but this "
                "combination was not tested." for k in now if k in built and built[k] != now[k]]

    def info(self) -> dict:
        c, m = self.pipe.cfg, self.manifest
        return {
            "name": "CubeSat solar-panel kNN anomaly detector", "package_version": m.get("package_version"),
            "feature_set": c.feature_set, "level": c.level, "features": list(self.pipe.feature_names_),
            "k": c.k, "scaler": c.scaler, "threshold_percentile": c.threshold_percentile,
            "threshold": float(self.pipe.det.threshold_), "reference_rows": m.get("reference_rows"),
            "reference_sessions": m.get("n_training_sessions"), "reference_by_satellite": m.get("training_sources_by_satellite"),
            "generated_utc": m.get("generated_utc"), "manifest_sha256": self._manifest_sha()[:16],
            "scale_guard": {"max_scale_ratio": self.pipe.max_scale_ratio,
                            "reference_p99": self.pipe.provenance.get("reference_feature_p99")},
            "software_built_with": m.get("software"),
            "software_running": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                                 "scikit-learn": sklearn.__version__, "scipy": scipy.__version__},
            "version_warnings": self.version_warnings,
        }

    def _manifest_sha(self) -> str:
        import hashlib
        return hashlib.sha256((self.model_dir / "manifest.json").read_bytes()).hexdigest()

    # ------------------------------------------------------------------ compatibility + inference
    def features(self, table: pd.DataFrame) -> pd.DataFrame:
        return build_channel_features(table, self.pipe.cfg)

    def check_scale(self, features: pd.DataFrame) -> list[dict]:
        """Display-only copy of the guard's ratios; the accept/reject decision is the model package's own check_compatibility."""
        ref = self.pipe.provenance["reference_feature_p99"]; lim = self.pipe.max_scale_ratio; out = []
        for n in ("V_V", "I_mA"):
            if n in features and n in ref and ref[n] > 0:
                col = features[n].to_numpy(float); col = col[np.isfinite(col)]
                if len(col) == 0:
                    out.append({"feature": n, "ratio": None, "ok": False}); continue
                r = float(np.percentile(col, 99)) / ref[n]
                out.append({"feature": n, "ratio": r, "ok": (1 / lim) <= r <= lim, "allowed": [1 / lim, lim]})
        return out

    def assert_compatible(self, features: pd.DataFrame) -> None:
        self.pipe.check_compatibility(features)        # raises IncompatibleBaselineError

    def predict(self, table: pd.DataFrame) -> pd.DataFrame:
        with self._lock:
            return self.pipe.predict(table)
