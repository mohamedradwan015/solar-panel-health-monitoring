"""End-to-end interface: fit / predict / evaluate / save_artifacts / load_trusted_artifacts."""
from __future__ import annotations
import json, hashlib, platform, time
from pathlib import Path
import numpy as np, pandas as pd, sklearn, scipy
from .config import DetectorConfig, RuleLabelConfig
from .features import (build_channel_features, build_satellite_features, feature_names, rule_labels, KEYS,
                       FeatureSchemaError, channels_in)
from .knn_detector import KNNDetector
from .diagnostics import diagnose, DEFINITIONS
from . import severity as sev
from . import __version__


class IncompatibleBaselineError(ValueError):
    """The new data cannot be scored against this baseline without recalibration."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CubeSatKNNPipeline:
    def __init__(self, cfg: DetectorConfig | None = None, rules: RuleLabelConfig | None = None,
                 max_scale_ratio: float = 2.0):
        self.cfg = cfg or DetectorConfig(); self.rules = rules or RuleLabelConfig()
        self.max_scale_ratio = max_scale_ratio
        self.det: KNNDetector | None = None
        self.provenance: dict = {}
        self.severity_base: dict | None = None

    # ------------------------------------------------------------------ features
    def _features(self, table: pd.DataFrame) -> pd.DataFrame:
        F = build_channel_features(table, self.cfg)
        if self.cfg.level == "satellite":
            F = build_satellite_features(F, table, self.cfg)
        return F

    # ------------------------------------------------------------------ fit
    def fit(self, tables: list[pd.DataFrame], reference_mask=None, calibrate: bool = True,
            kmax_for_calibration: int | None = None, channel_exclusions=None, ledger_sha256: str | None = None):
        """Fit on trusted-normal observations only.
        reference_mask: optional callable(feature_frame)->bool array. Default: flight mode, no published-rule anomaly on
        any channel of that observation, not an all-channel-zero readout glitch."""
        cfg = self.cfg
        frames, rls = [], []
        n_masked = 0
        if channel_exclusions:                      # ledger-driven: excluded (satellite, session, channel) are set to MISSING
            ex = set(channel_exclusions); masked = []
            for t in tables:
                t = t.copy()
                for (sat, ses, ch) in ex:
                    m = ((t["satellite"] == sat) & (t["session"] == ses)).values
                    if m.any():
                        cols = [c for c in (f"V:{ch}", f"I:{ch}", f"T:{ch}") if c in t.columns]
                        t.loc[m, cols] = np.nan; n_masked += int(m.sum())
                masked.append(t)
            tables = masked
        for t in tables:
            F = self._features(t); frames.append(F); rls.append(rule_labels(t, self.rules))
        F = pd.concat(frames, ignore_index=True); R = pd.concat(rls, ignore_index=True)
        if cfg.level == "channel":
            F = F.merge(R[KEYS + ["rule_anomaly"]], on=KEYS, how="left")
        else:
            Ra = R.groupby(["satellite", "session", "source_row"], sort=False)["rule_anomaly"].any().reset_index()
            F = F.merge(Ra, on=["satellite", "session", "source_row"], how="left")
        F["rule_anomaly"] = F["rule_anomaly"].fillna(False).astype(bool)
        mask = (F["mode"] == "flight") & ~F["rule_anomaly"] & ~F["all_channels_zero_V"]
        if cfg.level == "channel": mask &= F["V_mV"].notna() & F["I_mA"].notna()   # masked / missing channels never enter the reference
        if reference_mask is not None: mask &= np.asarray(reference_mask(F), bool)
        names = feature_names(cfg)
        Ref = F.loc[mask]
        Xall = Ref[names].to_numpy(float)
        ok = ~np.isnan(Xall).all(axis=1); Ref, X = Ref.loc[ok], Xall[ok]
        if len(X) < cfg.min_reference:
            raise ValueError(f"only {len(X)} trusted-normal reference rows (< min_reference={cfg.min_reference})")
        if cfg.max_reference and len(X) > cfg.max_reference:
            rs = np.random.RandomState(cfg.random_state); sel = np.sort(rs.choice(len(X), cfg.max_reference, replace=False))
            self.provenance["reference_subsampled_from"] = int(len(X)); Ref, X = Ref.iloc[sel], X[sel]
        self.severity_base = sev.fit_baseline(Ref) if cfg.level == "channel" else {"available": False, "reason": "satellite-level model"}
        self.det = KNNDetector(cfg.k, cfg.scaler, cfg.random_state).fit(X, names)
        groups = (Ref["satellite"].astype(str) + "|" + Ref["session"].astype(str)).values
        self.provenance.update({
            "reference_rows": int(len(X)), "reference_by_satellite": Ref["satellite"].value_counts().to_dict(),
            "reference_sessions": int(len(set(groups))), "rows_excluded_rule_anomaly": int(F["rule_anomaly"].sum()),
            "rows_excluded_allzero_glitch": int(F["all_channels_zero_V"].sum()),
            "rows_excluded_non_flight": int((F["mode"] != "flight").sum()),
            "channel_exclusion_rows_masked": int(n_masked), "ledger_sha256": ledger_sha256,
            "sources": sorted({f"{a}|{b}" for a, b in zip(Ref["satellite"], Ref["session"])}),
            "threshold_method": f"percentile {cfg.threshold_percentile} of out-of-session mean-kNN-distance (k={cfg.k}) over trusted-normal reference; leave-one-session-out folds (5 contiguous blocks if <4 sessions)",
            "reference_rows_with_imputed_feature_values": int(np.isnan(X).any(axis=1).sum()),
            "constant_features": [names[i] for i in self.det.constant_features_],
            "reference_feature_p99": {n: float(np.nanpercentile(X[:, i], 99)) for i, n in enumerate(names)},
            "reference_feature_p1": {n: float(np.nanpercentile(X[:, i], 1)) for i, n in enumerate(names)}})
        if calibrate:
            km = kmax_for_calibration or cfg.k
            D = KNNDetector.grouped_distances(X, groups, names, km, cfg.scaler)
            s = D[:, :cfg.k].mean(axis=1)
            self.det.set_threshold(s, cfg.threshold_percentile)
            self._calib_D, self._calib_groups = D, groups   # kept in memory for model selection, not saved
        self.provenance["fit_time_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.feature_names_ = names
        self.channel_signature_ = {"level": cfg.level, "feature_set": cfg.feature_set}
        return self

    def retune(self, k: int | None = None, percentile: float | None = None):
        """Change k / threshold percentile using stored out-of-group calibration distances (no refit of scaler)."""
        k = k or self.cfg.k; percentile = percentile or self.cfg.threshold_percentile
        if k > self._calib_D.shape[1]: raise ValueError("k exceeds kmax used for calibration; refit with larger kmax_for_calibration")
        self.cfg.k, self.cfg.threshold_percentile = k, percentile; self.det.k = k
        self.det.nn_ = self.det.nn_.set_params(n_neighbors=k)
        self.det.set_threshold(self._calib_D[:, :k].mean(axis=1), percentile)

    # ------------------------------------------------------------------ compatibility
    def check_compatibility(self, F: pd.DataFrame) -> list[str]:
        warn = []
        p99 = self.provenance["reference_feature_p99"]
        for n in ("V_V", "I_mA", "sun_V"):
            if n in F and n in p99 and p99[n] > 0:
                r = float(np.nanpercentile(F[n], 99)) / p99[n]
                if r > self.max_scale_ratio or r < 1 / self.max_scale_ratio:
                    raise IncompatibleBaselineError(
                        f"feature {n}: new-data 99th percentile is {r:.2f}x the reference baseline "
                        f"(allowed {1/self.max_scale_ratio:.2f}-{self.max_scale_ratio:.2f}x). Units/scale/hardware differ: "
                        f"this baseline cannot be applied; calibrate a new baseline for this satellite.")
        return warn

    # ------------------------------------------------------------------ predict
    def predict(self, table: pd.DataFrame, check_compat: bool = True) -> pd.DataFrame:
        if self.det is None or not hasattr(self.det, "threshold_"): raise RuntimeError("fit (with calibration) or load artifacts first")
        if table is None or len(table) == 0: raise ValueError("empty input table: nothing to score")
        F = self._features(table)           # raises FeatureSchemaError for unsupported schemas
        if len(self.feature_names_) != len(self.det.feature_names_) or list(self.feature_names_) != list(self.det.feature_names_):
            raise FeatureSchemaError("feature order mismatch between config and fitted model")
        if check_compat: self.check_compatibility(F)
        X = F[self.feature_names_].to_numpy(float)
        score = np.full(len(F), np.nan); has_v = ~np.isnan(F["V_mV"].values) if "V_mV" in F else np.ones(len(F), bool)
        usable = has_v & ~np.isnan(X).all(axis=1)
        score[usable] = self.det.score(X[usable])
        P = F[[c for c in ["satellite", "session", "source_row", "channel", "t_s", "t_imputed", "mode", "V_mV", "I_mA",
                           "all_channels_zero_V"] if c in F.columns]].copy()
        P["score"] = score; P["threshold"] = self.det.threshold_
        P["anomaly"] = np.where(np.isnan(score), False, score > self.det.threshold_)
        nanfeat = np.isnan(X).any(axis=1)
        P["quality_warnings"] = [";".join(w for w, c in (("imputed_timestamp", ti), ("feature_imputed(warmup/missing)", nf),
                                                         ("all_channels_V_zero", az), ("missing_voltage", mv)) if c)
                                 for ti, nf, az, mv in zip(P["t_imputed"], nanfeat, P["all_channels_zero_V"], ~has_v)]
        P["data_quality_status"] = np.where(np.isnan(score), "unscorable", np.where(P["quality_warnings"] != "", "warn", "ok"))
        loss, sstat = sev.estimate(F, P["anomaly"].values, self.severity_base)
        P["power_loss_est"] = loss; P["severity_status"] = sstat
        contrib = None
        fl = np.where(P["anomaly"].values)[0]
        if len(fl):
            dev = self.det.neighbour_deviation(X[fl]); contrib = pd.DataFrame(np.nan, index=P.index, columns=self.feature_names_)
            contrib.iloc[fl] = dev
        dts = {}
        for ses, g in P.groupby("session"):
            d = np.diff(np.sort(g["t_s"].dropna().unique())); d = d[d > 0]
            dts[ses] = int(max(3, round(self.cfg.window_seconds / (np.median(d) if len(d) else 1.0))))
        P = P.reset_index(drop=True)
        if contrib is not None: contrib = contrib.reset_index(drop=True)
        out = diagnose(P, contrib, dts)
        # integrity: nothing lost
        n_expect = len(table) * len(channels_in(table)) if self.cfg.level == "channel" else len(table)
        if len(out) != n_expect: raise RuntimeError(f"prediction rows {len(out)} != expected {n_expect}")
        return out

    # ------------------------------------------------------------------ evaluate
    def evaluate(self, table: pd.DataFrame, labels: pd.DataFrame | None = None, label_col: str = "label") -> dict:
        """labels: frame with KEYS (or satellite/session/source_row for satellite level) + bool column label_col."""
        from .evaluation import binary_metrics
        P = self.predict(table)
        res = {"n": int(len(P)), "alert_rate": float(P["anomaly"].mean()), "alerts": int(P["anomaly"].sum()),
               "score_summary": P["score"].describe().round(4).to_dict(),
               "labels_supplied": labels is not None}
        if labels is not None:
            keys = KEYS if self.cfg.level == "channel" else ["satellite", "session", "source_row"]
            M = P.merge(labels[keys + [label_col]], on=keys, how="left")
            if M[label_col].isna().any(): raise ValueError("labels missing for some predictions; refusing partial evaluation")
            res.update(binary_metrics(M[label_col].astype(bool).values, M["score"].values, M["anomaly"].values))
        return res

    # ------------------------------------------------------------------ artifacts
    def save_artifacts(self, outdir) -> Path:
        d = Path(outdir); d.mkdir(parents=True, exist_ok=True)
        W = lambda name, obj: (d / name).write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n")
        W("config.json", {"detector": self.cfg.to_dict(), "rules": self.rules.__dict__, "max_scale_ratio": self.max_scale_ratio, "package_version": __version__})
        W("feature_schema.json", {"feature_names": self.feature_names_, "signature": self.channel_signature_, "diagnosis_definitions": DEFINITIONS})
        m = self.det
        np.savez(d / "model_arrays.npz", median=m.median_, center=m.center_, scale=m.scale_, reference=m.ref_)   # uncompressed, fixed order
        W("thresholds.json", {"threshold": m.threshold_, "percentile": m.threshold_percentile_, "k": m.k,
                              "calibration_scores_summary": m.calibration_scores_summary_})
        W("provenance.json", self.provenance)
        W("severity_baseline.json", self.severity_base or {"available": False, "reason": "not fitted"})
        MODEL_FILES = ["config.json", "feature_schema.json", "model_arrays.npz", "provenance.json", "severity_baseline.json", "thresholds.json"]
        files = {n: {"sha256": _sha(d / n), "bytes": (d / n).stat().st_size} for n in MODEL_FILES}
        man = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "package_version": __version__,
               "feature_set": self.cfg.feature_set, "level": self.cfg.level, "k": m.k, "threshold": m.threshold_,
               "threshold_method": self.provenance.get("threshold_method"), "reference_rows": int(len(m.ref_)),
               "training_sources_by_satellite": self.provenance.get("reference_by_satellite"),
               "n_training_sessions": len(self.provenance.get("sources", [])), "ledger_sha256": self.provenance.get("ledger_sha256"),
               "exclusions": {k: self.provenance.get(k) for k in ("rows_excluded_rule_anomaly", "rows_excluded_allzero_glitch",
                                                                   "rows_excluded_non_flight", "channel_exclusion_rows_masked")},
               "config": self.cfg.to_dict(),
               "software": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                            "scikit-learn": sklearn.__version__, "scipy": scipy.__version__},
               "files": files, "format_note": "JSON + NumPy .npz (allow_pickle=False). No pickled Python objects."}
        W("manifest.json", man)
        return d

    @classmethod
    def load_trusted_artifacts(cls, outdir, expected_manifest_sha256: str | None = None):
        """Integrity-checked load. 'Trusted' = files match the manifest (and optionally a manifest hash you recorded
        out-of-band). This detects corruption/tampering of individual files, not a malicious manifest+files pair."""
        d = Path(outdir); mp = d / "manifest.json"
        if expected_manifest_sha256 and _sha(mp) != expected_manifest_sha256:
            raise ValueError("manifest hash does not match the expected value; refusing to load")
        man = json.loads(mp.read_text())
        for name, h in man["files"].items():
            h = h["sha256"] if isinstance(h, dict) else h          # new manifests store {sha256, bytes}; v0.1 stored the hash string
            if _sha(d / name) != h: raise ValueError(f"integrity check failed for {name}; refusing to load")
        c = json.loads((d / "config.json").read_text())
        obj = cls(DetectorConfig.from_dict(c["detector"]), RuleLabelConfig(**c["rules"]), c["max_scale_ratio"])
        fs = json.loads((d / "feature_schema.json").read_text()); th = json.loads((d / "thresholds.json").read_text())
        z = np.load(d / "model_arrays.npz", allow_pickle=False)
        det = KNNDetector(th["k"], obj.cfg.scaler, obj.cfg.random_state)
        det.median_, det.center_, det.scale_, det.ref_ = z["median"], z["center"], z["scale"], z["reference"]
        det.feature_names_ = fs["feature_names"]; det.constant_features_ = []
        det._build(); det.fitted = True
        det.threshold_, det.threshold_percentile_ = th["threshold"], th["percentile"]
        det.calibration_scores_summary_ = th["calibration_scores_summary"]
        obj.det = det; obj.feature_names_ = fs["feature_names"]; obj.channel_signature_ = fs["signature"]
        obj.provenance = json.loads((d / "provenance.json").read_text())
        sp = d / "severity_baseline.json"
        obj.severity_base = json.loads(sp.read_text()) if sp.exists() else {"available": False, "reason": "artifact predates severity baseline"}
        return obj
