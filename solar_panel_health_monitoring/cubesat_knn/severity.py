"""Gated power-loss estimate. NOT derived from the KNN score.

Basis: spin-averaged panels see comparable illumination, so a panel's rolling 90th-percentile current relative to the best
sibling (i_lit_ratio) has a healthy reference distribution. loss = 1 - ratio / healthy_median. This is only meaningful when
siblings are clearly lit, the window is full, the observation is flagged, and the estimate exceeds the healthy spread.
Panel-level telemetry cannot localise faults to individual cells.
"""
from __future__ import annotations
import numpy as np, pandas as pd

GATE_SIB_I_MA = 100.0     # siblings' rolling q90 current must exceed this (clear illumination)
GATE_SUN_V = 4.0


def fit_baseline(F: pd.DataFrame) -> dict:
    """F: reference feature frame (trusted-normal rows). Returns healthy ratio statistics under the same gate."""
    if "i_lit_ratio" not in F or "sib_i_q90_mA" not in F: return {"available": False, "reason": "single-channel reference (no sibling statistics)"}
    g = F[(F["sib_i_q90_mA"] >= GATE_SIB_I_MA) & (F["sun_V"] >= GATE_SUN_V)]["i_lit_ratio"].dropna()
    if len(g) < 500: return {"available": False, "reason": f"only {len(g)} gated reference rows"}
    return {"available": True, "n": int(len(g)), "healthy_median": float(g.median()), "healthy_p05": float(g.quantile(.05)),
            "healthy_p95": float(g.quantile(.95)), "gate_sib_i_mA": GATE_SIB_I_MA, "gate_sun_V": GATE_SUN_V}


def estimate(F: pd.DataFrame, anomaly: np.ndarray, base: dict | None):
    """Returns (loss array, status array)."""
    n = len(F); loss = np.full(n, np.nan); status = np.full(n, "not_flagged", dtype=object)
    if not base or not base.get("available"):
        return loss, np.where(anomaly, "unscorable_no_baseline", "not_flagged").astype(object)
    if "i_lit_ratio" not in F:
        return loss, np.where(anomaly, "unscorable_single_channel", "not_flagged").astype(object)
    ratio = F["i_lit_ratio"].to_numpy(float); sib = F["sib_i_q90_mA"].to_numpy(float); sun = F["sun_V"].to_numpy(float)
    gate = (sib >= base["gate_sib_i_mA"]) & (sun >= base["gate_sun_V"]) & ~np.isnan(ratio)
    res = max(0.0, 1.0 - base["healthy_p05"] / base["healthy_median"])      # healthy spread = smallest resolvable loss
    est = np.clip(1.0 - ratio / base["healthy_median"], 0.0, 1.0)
    fl = np.asarray(anomaly, bool)
    status[fl & ~gate] = "unscorable_low_illumination_or_missing"
    ok = fl & gate
    status[ok & (est <= res)] = "within_healthy_spread"
    status[ok & (est > res)] = "estimated"
    loss[ok & (est > res)] = est[ok & (est > res)]
    return loss, status
