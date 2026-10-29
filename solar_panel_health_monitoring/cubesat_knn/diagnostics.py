"""Cautious, evidence-limited labels on top of KNN scores. None of these confirm a physical fault."""
from __future__ import annotations
import numpy as np, pandas as pd

OUTPUT_FEATURES = {"V_V", "I_mA", "lit_ratio", "i_lit_ratio", "v_rel_med", "v_rel_max",
                   "lit_ratio_min", "lit_ratio_med", "i_lit_ratio_min", "i_lit_ratio_med", "v_rel_max_min"}
DEFINITIONS = {
    "normal_like": "score <= threshold",
    "statistical_anomaly": "score > threshold; no dominant output-level feature or direction",
    "suspected_low_output": "flagged and the dominant deviating output feature is BELOW its neighbours (V, I or relative-to-sibling)",
    "suspected_high_output": "flagged and the dominant deviating output feature is ABOVE its neighbours",
    "persistent_channel_deviation": "flagged in >= persistence_fraction of the last window_seconds of this channel (causal)",
    "data_quality_issue": "all channel voltages are exactly 0 at the same instant (readout/telemetry glitch pattern) or inputs missing",
    "unknown_or_insufficient_evidence": "no score (missing voltage) -> not evaluated",
}


def diagnose(df: pd.DataFrame, contrib: pd.DataFrame | None, window_samples: dict | None = None,
             persistence_fraction: float = 0.8) -> pd.DataFrame:
    """df: predictions (needs anomaly, score, all_channels_zero_V, session, channel, t_s). contrib: signed deviation per feature."""
    cat = np.where(df["anomaly"], "statistical_anomaly", "normal_like").astype(object)
    direction = np.full(len(df), "", dtype=object); top = np.full(len(df), "", dtype=object)
    flagged = np.where(df["anomaly"].values)[0]
    if contrib is not None and len(flagged):
        C = contrib.iloc[flagged]
        mag = C.abs().values; order = np.argsort(-mag, axis=1)[:, :3]
        cols = np.array(C.columns)
        for r, row in enumerate(flagged):
            names = cols[order[r]]
            top[row] = ";".join(f"{n}({C.iloc[r][n]:+.1f})" for n in names)
            lead_out = [n for n in names if n in OUTPUT_FEATURES]
            if lead_out and lead_out[0] == names[0]:
                s = C.iloc[r][lead_out[0]]
                direction[row] = "low" if s < 0 else "high"
                cat[row] = "suspected_low_output" if s < 0 else "suspected_high_output"
    # causal persistence per (session, channel)
    pers = np.zeros(len(df), bool)
    for (ses, ch), g in df.groupby(["session", "channel"], sort=False):
        w = (window_samples or {}).get(ses, 18)
        f = g.sort_values(["t_s", "source_row"], kind="stable")["anomaly"].astype(float)
        frac = f.rolling(w, min_periods=w).mean()
        pers[df.index.get_indexer(frac.index[(frac >= persistence_fraction).values])] = True
    cat = np.where(pers & df["anomaly"].values, "persistent_channel_deviation", cat)
    cat = np.where(df["all_channels_zero_V"].values, "data_quality_issue", cat)
    cat = np.where(df["score"].isna(), "unknown_or_insufficient_evidence", cat)
    out = df.copy(); out["diagnosis"] = cat; out["direction"] = direction; out["top_deviations"] = top
    return out
