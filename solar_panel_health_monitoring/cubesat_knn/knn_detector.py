"""Distance-to-k-nearest-normal-neighbours anomaly scorer. Parameters are plain arrays (JSON/NPZ-serialisable)."""
from __future__ import annotations
import numpy as np
from sklearn.neighbors import NearestNeighbors


class KNNDetector:
    def __init__(self, k: int = 10, scaler: str = "robust", random_state: int = 0):
        if k < 1: raise ValueError("k must be >= 1")
        self.k, self.scaler, self.random_state = k, scaler, random_state
        self.fitted = False

    # ---- preprocessing (fit on reference only) ----
    @staticmethod
    def _clean(X):
        X = np.asarray(X, float).copy(); X[~np.isfinite(X)] = np.nan   # +-inf is treated as missing, never fed to distances
        return X

    def _fit_prep(self, X: np.ndarray):
        X = self._clean(X)
        self.median_ = np.nanmedian(X, axis=0)
        if np.isnan(self.median_).any(): raise ValueError("a feature is entirely NaN in the reference data")
        Xi = np.where(np.isnan(X), self.median_, X)
        if self.scaler == "robust":
            self.center_ = np.median(Xi, axis=0)
            q75, q25 = np.percentile(Xi, [75, 25], axis=0); sc = q75 - q25
        elif self.scaler == "standard":
            self.center_ = Xi.mean(axis=0); sc = Xi.std(axis=0)
        else:
            raise ValueError("scaler must be 'robust' or 'standard'")
        self.constant_features_ = np.where(sc <= 1e-12)[0].tolist()
        self.scale_ = np.where(sc <= 1e-12, 1.0, sc)

    def transform(self, X: np.ndarray) -> np.ndarray:
        X = self._clean(X)
        if X.shape[1] != len(self.median_):
            raise ValueError(f"expected {len(self.median_)} features, got {X.shape[1]}")
        return (np.where(np.isnan(X), self.median_, X) - self.center_) / self.scale_

    def fit(self, X: np.ndarray, feature_names: list[str]):
        X = self._clean(X)
        if X.ndim != 2 or len(X) <= self.k:
            raise ValueError(f"need more reference rows ({len(X)}) than k ({self.k})")
        self.feature_names_ = list(feature_names)
        self._fit_prep(X)
        self.ref_ = self.transform(X)
        self._build()
        self.fitted = True
        return self

    def _build(self):
        self.nn_ = NearestNeighbors(n_neighbors=min(self.k, len(self.ref_)), algorithm="auto").fit(self.ref_)

    # ---- scoring ----
    def score(self, X: np.ndarray, batch: int = 20000) -> np.ndarray:
        Z = self.transform(X); out = np.empty(len(Z))
        for i in range(0, len(Z), batch):
            d, _ = self.nn_.kneighbors(Z[i:i + batch], n_neighbors=self.k)
            out[i:i + batch] = d.mean(axis=1)
        return out

    def neighbour_deviation(self, X: np.ndarray):
        """Scaled difference between each row and the mean of its k nearest reference neighbours."""
        Z = self.transform(X); _, idx = self.nn_.kneighbors(Z, n_neighbors=self.k)
        return Z - self.ref_[idx].mean(axis=1)

    # ---- calibration ----
    @staticmethod
    def grouped_distances(X, groups, feature_names, kmax, scaler="robust", n_blocks=5, row_order_within_group=True):
        """Out-of-group k-NN distance matrix (n, kmax) for every reference row.
        Each fold scores one group using a model fitted on all *other* groups, so temporally adjacent
        samples cannot make the calibration scores optimistic. With < 4 groups, contiguous time blocks are used."""
        groups = np.asarray(groups).astype(str)
        uniq = list(dict.fromkeys(groups))
        if len(uniq) < 4:
            blocks = np.empty(len(groups), dtype=object)
            for g in uniq:
                ix = np.where(groups == g)[0]
                for b, part in enumerate(np.array_split(ix, n_blocks)): blocks[part] = f"{g}#{b}"
            groups = blocks.astype(str); uniq = list(dict.fromkeys(groups))
        D = np.full((len(X), kmax), np.nan)
        for g in uniq:
            te = groups == g
            if (~te).sum() <= kmax: continue
            m = KNNDetector(k=kmax, scaler=scaler).fit(X[~te], feature_names)
            Z = m.transform(X[te]); d, _ = m.nn_.kneighbors(Z, n_neighbors=kmax)
            D[te] = d
        return D

    def set_threshold(self, calib_scores: np.ndarray, percentile: float):
        s = np.asarray(calib_scores); s = s[~np.isnan(s)]
        if len(s) < 50: raise ValueError(f"only {len(s)} calibration scores; threshold would be unstable")
        self.threshold_ = float(np.percentile(s, percentile)); self.threshold_percentile_ = percentile
        self.calibration_scores_summary_ = {"n": int(len(s)), "p50": float(np.percentile(s, 50)),
                                            "p95": float(np.percentile(s, 95)), "p99": float(np.percentile(s, 99)),
                                            "p99.9": float(np.percentile(s, 99.9)), "max": float(s.max())}
        return self.threshold_
