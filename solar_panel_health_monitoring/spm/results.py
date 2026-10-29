"""Turns raw model output into what the interface shows: panel statistics, flagged intervals, chart series.

Everything here is derived arithmetic on the model's own outputs (score, threshold, anomaly, diagnosis). No value is
estimated, smoothed or invented. Flags are never re-thresholded.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
import numpy as np, pandas as pd
from . import settings
from .model_service import ModelService


class AnalysisError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message); self.code, self.message, self.status = code, message, status


def natural_key(s: str):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(s))]


def to_jsonable(o):
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return to_jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


TRIAGE_LABELS = {"insufficient": "Insufficient data", "high": "High alert rate", "review": "Review",
                 "within_expected": "Within expected alert rate"}


def triage(n_scored: int, pct: float | None, persistent: int) -> dict:
    """Application triage (NOT a model output, NOT a fault classification). Thresholds: settings.TRIAGE_*."""
    if n_scored < settings.TRIAGE_MIN_SCORED or pct is None:
        lvl = "insufficient"
    elif pct >= settings.TRIAGE_HIGH_MIN_PCT:
        lvl = "high"
    elif pct > settings.TRIAGE_WITHIN_EXPECTED_MAX_PCT or persistent > 0:
        lvl = "review"
    else:
        lvl = "within_expected"
    return {"level": lvl, "label": TRIAGE_LABELS[lvl], "color": {"insufficient": "gray", "high": "red", "review": "amber", "within_expected": "green"}[lvl]}


def minmax_keep_mask(arrays: list[np.ndarray], always: np.ndarray, max_points: int) -> np.ndarray:
    """Rows to keep when drawing: first/last, every row in `always` (flagged readings), and the min and max of every
    series in each bucket, so spikes and dropouts survive decimation."""
    n = len(always)
    if n <= max_points:
        return np.ones(n, bool)
    buckets = max(1, max_points // (2 * len(arrays) + 2)); size = int(np.ceil(n / buckets)); buckets = int(np.ceil(n / size))
    keep = always.copy(); keep[0] = keep[-1] = True
    for a in arrays:
        A = np.full(buckets * size, np.nan); A[:n] = a; A = A.reshape(buckets, size)
        fin = np.isfinite(A); any_fin = fin.any(1)
        imin = np.where(fin, A, np.inf).argmin(1); imax = np.where(fin, A, -np.inf).argmax(1)
        base = np.arange(buckets) * size
        keep[(base + imin)[any_fin]] = True; keep[(base + imax)[any_fin]] = True
    return keep


def _runs(mask: np.ndarray):
    """Start/end indices (inclusive) of consecutive True runs."""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    return np.where(d == 1)[0], np.where(d == -1)[0] - 1


def _r(x, nd):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _nullable(a: np.ndarray, nd: int):
    s = pd.Series(np.round(a, nd)).astype(object)
    return s.where(pd.notna(s), None).tolist()


@dataclass
class Analysis:
    summary: dict
    predictions: pd.DataFrame
    intervals: pd.DataFrame
    flagged: pd.DataFrame
    series: dict            # session -> JSON-ready dict


def analyse(model: ModelService, tables: list, original_name: str, satellite: str) -> Analysis:
    frames, iso_present = [], False
    for name, canon, meta in tables:
        P = model.predict(canon)                         # <- the supplied model, unchanged
        P["panel"] = P["channel"].map(lambda c: meta["label_map"].get(c, c))
        if "timestamp_iso" in canon.columns:
            iso = canon.set_index("source_row")["timestamp_iso"]
            P["timestamp_utc"] = P["source_row"].map(iso).astype(object); iso_present = True
        P.attrs["observations"] = len(canon)
        frames.append((name, P, canon))
    P = pd.concat([f[1] for f in frames], ignore_index=True)
    score = P["score"].to_numpy(float)
    if np.isinf(score).any():
        raise AnalysisError("non_finite_scores", "The model returned non-finite scores; the run was discarded.", 500)
    if int(np.isfinite(score).sum()) == 0:
        raise AnalysisError("nothing_scored", "No reading could be scored: every voltage in the selected data is missing.")
    thr = float(P["threshold"].iloc[0])

    sessions, series, interval_rows = [], {}, []
    for name, G, canon in frames:
        sess, ser, iv = _session(name, G, canon, thr)
        sess["mode"] = next(m["mode"] for n, c, m in tables if n == name)
        sessions.append(sess); series[name] = ser; interval_rows += iv

    iv_df = pd.DataFrame(interval_rows, columns=["session", "panel", "start_t_s", "end_t_s", "start_timestamp_utc", "end_timestamp_utc",
                                                 "flagged_readings", "duration_s", "max_score", "threshold", "dominant_diagnosis"])
    fl = P[P["anomaly"]].copy()
    fl["V_V"] = fl["V_mV"] / 1000.0
    cols = ["session", "panel", "timestamp_utc", "t_s", "V_V", "I_mA", "score", "threshold", "diagnosis", "direction", "top_deviations"]
    sess_rank = {n: i for i, (n, _, _) in enumerate(tables)}
    panel_rank = {p: i for i, p in enumerate(sorted(P["panel"].unique(), key=natural_key))}
    fl["_s"] = fl["session"].map(sess_rank); fl["_p"] = fl["panel"].map(panel_rank)
    flagged = fl.sort_values(["_s", "_p", "t_s", "source_row"], kind="stable")[[c for c in cols if c in fl.columns]].reset_index(drop=True)

    lead = [c for c in ("satellite", "session", "source_row", "panel", "channel", "timestamp_utc", "t_s") if c in P.columns]
    P = P[lead + [c for c in P.columns if c not in lead]]
    n_scored = int(np.isfinite(score).sum()); n_flag = int(P["anomaly"].sum())
    all_panels = sorted({p["panel"] for s in sessions for p in s["panels"]}, key=natural_key)
    overview = {"panels": len(all_panels), "panel_names": all_panels, "sessions": len(sessions),
                "observations": int(sum(len(f[2]) for f in frames)), "panel_readings": int(len(P)), "scored_readings": n_scored,
                "unscorable_readings": int(len(P) - n_scored), "flagged_readings": n_flag,
                "flagged_pct_of_scored": _r(100.0 * n_flag / n_scored, 4) if n_scored else None, "intervals": int(len(iv_df))}
    summary = {"status": "complete", "input_name": original_name, "satellite_label": satellite, "overview": overview, "sessions": sessions,
               "threshold": thr, "has_timestamps": iso_present,
               "triage_rules": {"min_scored": settings.TRIAGE_MIN_SCORED, "within_expected_max_pct": settings.TRIAGE_WITHIN_EXPECTED_MAX_PCT,
                                "high_min_pct": settings.TRIAGE_HIGH_MIN_PCT}}
    return Analysis(to_jsonable(summary), P, iv_df, flagged, series)


def _session(name: str, G: pd.DataFrame, canon: pd.DataFrame, thr: float):
    chans = sorted(G["channel"].unique(), key=natural_key)
    obs = G.drop_duplicates("source_row")[["source_row", "t_s"]]
    valid = obs[obs["t_s"].notna()].sort_values(["t_s", "source_row"], kind="stable")
    rows = valid["source_row"].to_numpy(); t = valid["t_s"].to_numpy(float)
    d = np.diff(t); pos = d[d > 0]; dt = float(np.median(pos)) if len(pos) else 1.0
    gap = float(max(5 * dt, 1.0)) if len(pos) else 0.0
    iso_map = canon.set_index("source_row")["timestamp_iso"] if "timestamp_iso" in canon.columns else None

    panels, vcols, scols, flags, ivs = [], [], [], [], []
    for ch in chans:
        g = G[G["channel"] == ch]
        label = g["panel"].iloc[0]
        a = g.set_index("source_row").reindex(rows)
        V = a["V_mV"].to_numpy(float) / 1000.0; S = a["score"].to_numpy(float); A = a["anomaly"].fillna(False).to_numpy(bool)
        diag = a["diagnosis"].astype(object).to_numpy()
        vcols.append(V); scols.append(S); flags.append(A)
        sc = g["score"].to_numpy(float); n_scored = int(np.isfinite(sc).sum()); n_flag = int(g["anomaly"].sum())
        pct = 100.0 * n_flag / n_scored if n_scored else None
        persistent = int((g["diagnosis"] == "persistent_channel_deviation").sum())
        glitch = int((g["diagnosis"] == "data_quality_issue").sum())
        counts = g.loc[g["anomaly"], "diagnosis"].value_counts().to_dict()
        s_idx, e_idx = _runs(A)
        for s0, e0 in zip(s_idx, e_idx):
            seg = diag[s0:e0 + 1]; vals, cnt = np.unique(seg.astype(str), return_counts=True)
            ivs.append({"session": name, "panel": label, "start_t_s": float(t[s0]), "end_t_s": float(t[e0]),
                        "start_timestamp_utc": str(iso_map.get(rows[s0])) if iso_map is not None and pd.notna(iso_map.get(rows[s0])) else None,
                        "end_timestamp_utc": str(iso_map.get(rows[e0])) if iso_map is not None and pd.notna(iso_map.get(rows[e0])) else None,
                        "flagged_readings": int(e0 - s0 + 1), "duration_s": float(t[e0] - t[s0] + dt),
                        "max_score": float(np.nanmax(S[s0:e0 + 1])), "threshold": thr, "dominant_diagnosis": str(vals[cnt.argmax()])})
        fin = V[np.isfinite(V)]
        last_v = float(fin[-1]) if len(fin) else None
        notes = []
        if n_scored < len(g): notes.append(f"{len(g) - n_scored} readings unscorable (missing voltage)")
        if int(g["t_imputed"].sum()): notes.append(f"{int(g['t_imputed'].sum())} timestamps extrapolated")
        if glitch: notes.append(f"{glitch} readings with every panel at exactly 0 V (readout-glitch pattern)")
        if persistent: notes.append(f"{persistent} flagged readings sit in persistent runs")
        panels.append({"panel": label, "channel": ch, "samples": int(len(g)), "scored": n_scored, "flagged": n_flag, "flagged_pct": _r(pct, 4),
                       "unscorable": int(len(g) - n_scored), "median_v": _r(np.nanmedian(g["V_mV"]) / 1000.0 if g["V_mV"].notna().any() else None, 4),
                       "last_v": _r(last_v, 4), "v_min": _r(float(fin.min()) if len(fin) else None, 4), "v_max": _r(float(fin.max()) if len(fin) else None, 4),
                       "median_score": _r(np.nanmedian(sc) if n_scored else None, 6), "max_score": _r(np.nanmax(sc) if n_scored else None, 6),
                       "threshold": thr, "persistent_flagged": persistent, "intervals": int(len(s_idx)), "diagnosis_counts": counts,
                       "triage": triage(n_scored, pct, persistent), "notes": notes})
    any_flag = np.any(flags, axis=0) if flags else np.zeros(len(t), bool)
    keep = minmax_keep_mask(vcols, any_flag, settings.DISPLAY_MAX_POINTS)
    pos_in_kept = np.cumsum(keep) - 1
    series = {"session": name, "t": _nullable(t[keep], 3), "gap_s": gap, "dt_s": dt, "unit": "V", "threshold": thr,
              "n_total": int(len(t)), "n_sent": int(keep.sum()), "decimated": bool(not keep.all()),
              "iso_start": str(iso_map.get(rows[0])) if iso_map is not None and len(rows) and pd.notna(iso_map.get(rows[0])) else None,
              "panels": [{"panel": p["panel"], "v": _nullable(v[keep], 4), "s": _nullable(s[keep], 6),
                          "f": pos_in_kept[np.where(f & keep)[0]].tolist()}
                         for p, v, s, f in zip(panels, vcols, scols, flags)],
              "intervals": [{"panel": r["panel"], "t0": r["start_t_s"], "t1": r["end_t_s"], "n": r["flagged_readings"],
                             "max_score": r["max_score"], "diag": r["dominant_diagnosis"]} for r in ivs]}
    n_unplot = int(G["t_s"].isna().sum())
    totals_scored = sum(p["scored"] for p in panels); totals_flag = sum(p["flagged"] for p in panels)
    sess = {"name": name, "observations": int(len(canon)), "panel_readings": int(len(G)), "scored": totals_scored, "flagged": totals_flag,
            "flagged_pct": _r(100.0 * totals_flag / totals_scored, 4) if totals_scored else None,
            "t_start": _r(t[0] if len(t) else None, 3), "t_end": _r(t[-1] if len(t) else None, 3), "cadence_median_s": _r(dt, 3),
            "iso_start": series["iso_start"], "unplottable_readings": n_unplot, "panels": panels,
            "intervals": int(len(ivs))}
    return sess, series, ivs
