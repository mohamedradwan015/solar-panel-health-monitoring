"""Metrics, synthetic fault injection with a separate truth table, experiment helpers."""
from __future__ import annotations
import numpy as np, pandas as pd
from sklearn.metrics import (precision_score, recall_score, f1_score, confusion_matrix, roc_auc_score,
                             average_precision_score, balanced_accuracy_score)


def binary_metrics(y, score, pred) -> dict:
    y = np.asarray(y, bool); pred = np.asarray(pred, bool); score = np.asarray(score, float)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[False, True]).ravel()
    out = {"tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn), "positives": int(y.sum()), "negatives": int((~y).sum()),
           "precision": float(tp / (tp + fp)) if tp + fp else np.nan, "recall": float(tp / (tp + fn)) if tp + fn else np.nan,
           "false_positive_rate": float(fp / (fp + tn)) if fp + tn else np.nan,
           "false_negative_rate": float(fn / (fn + tp)) if fn + tp else np.nan}
    p, r = out["precision"], out["recall"]
    out["f1"] = float(2 * p * r / (p + r)) if p == p and r == r and (p + r) > 0 else np.nan
    out["balanced_accuracy"] = float(np.nanmean([r, 1 - out["false_positive_rate"]]))
    ok = ~np.isnan(score)
    if y[ok].any() and (~y[ok]).any():
        out["roc_auc"] = float(roc_auc_score(y[ok], score[ok])); out["pr_auc"] = float(average_precision_score(y[ok], score[ok]))
    else:
        out["roc_auc"] = out["pr_auc"] = np.nan
    return out


# ------------------------------------------------------------------ synthetic fault injection
FAULT_TYPES = ["partial_degradation", "complete_zero_output", "stuck_cell_like", "sudden_shift", "intermittent_bursts", "noise_corruption"]


def inject_faults(table: pd.DataFrame, plan: list[dict], seed: int = 0):
    """Copy `table`, apply faults. plan item: {session, channel, type, start_frac, end_frac, severity}.
    Returns (modified copy, truth frame with exact parameters). The input is never modified."""
    rs = np.random.RandomState(seed)
    T = table.copy(); truth = []
    T = T.sort_values(["session", "t_s", "source_row"], kind="stable").reset_index(drop=True)
    T["_inj_"] = ""
    for p in plan:
        ix = np.where(T["session"].values == p["session"])[0]
        n = len(ix); a, b = int(p["start_frac"] * n), int(p["end_frac"] * n)
        seg = ix[a:b]; ch = p["channel"]; V, I = f"V:{ch}", f"I:{ch}"; ty = p["type"]; sev = p.get("severity", 0.5)
        lit = seg[T.loc[seg, V].values > 3500]        # illuminated samples (original values, before modification)
        if ty == "partial_degradation":        # current -sev, voltage -sev/8, only where the panel is illuminated
            T.loc[lit, I] = T.loc[lit, I] * (1 - sev); T.loc[lit, V] = T.loc[lit, V] * (1 - sev / 8); active = lit
        elif ty == "complete_zero_output":     # applies to every sample, lit or not
            T.loc[seg, V] = np.abs(rs.normal(1.5, 0.5, len(seg))); T.loc[seg, I] = 0.0; active = seg
        elif ty == "stuck_cell_like":          # voltage held 2000-3000 mV, current ~0 (shape of the published type-2 rule)
            T.loc[lit, V] = rs.uniform(2200, 2800, len(lit)); T.loc[lit, I] = rs.uniform(0, 5, len(lit)); active = lit
        elif ty == "sudden_shift":
            T.loc[lit, V] = T.loc[lit, V] * (1 - sev); T.loc[lit, I] = T.loc[lit, I] * (1 - sev); active = lit
        elif ty == "intermittent_bursts":
            blen = max(3, n // 100); starts = np.arange(a, b, max(blen * 4, 1)); act = []
            for s0 in starts:
                bs = ix[s0:s0 + blen]; bs = bs[T.loc[bs, V].values > 3500]
                T.loc[bs, I] = T.loc[bs, I] * (1 - sev); act += list(bs)
            active = np.array(act, int)
        elif ty == "noise_corruption":
            T.loc[seg, V] = np.clip(T.loc[seg, V] + rs.normal(0, sev * 1500, len(seg)), 0, None)
            T.loc[seg, I] = np.abs(T.loc[seg, I] + rs.normal(0, sev * 150, len(seg))); active = seg
        else: raise ValueError(ty)
        T.loc[active, "_inj_"] = ty
        for r in np.asarray(active, int):
            truth.append((T.at[r, "satellite"], p["session"], int(T.at[r, "source_row"]), ch, ty, sev))
    truth = pd.DataFrame(truth, columns=["satellite", "session", "source_row", "channel", "fault_type", "severity"])
    return T.drop(columns="_inj_"), truth


def labels_from_truth(table_channels: list[str], table: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    rows = [pd.DataFrame({"satellite": table["satellite"].values, "session": table["session"].values,
                          "source_row": table["source_row"].values, "channel": c}) for c in table_channels]
    L = pd.concat(rows, ignore_index=True)
    key = truth[["satellite", "session", "source_row", "channel"]].drop_duplicates().assign(label=True)
    L = L.merge(key, on=["satellite", "session", "source_row", "channel"], how="left")
    L["label"] = L["label"].fillna(False).astype(bool)
    return L


def detection_delay(pred: pd.DataFrame, truth_interval_start: dict) -> pd.DataFrame:
    """For each (session, channel) fault episode start (t_s), delay (s) to first alert at/after onset."""
    out = []
    for (ses, ch), t0 in truth_interval_start.items():
        g = pred[(pred.session == ses) & (pred.channel == ch) & (pred.t_s >= t0)].sort_values("t_s")
        hit = g[g.anomaly]
        out.append({"session": ses, "channel": ch, "onset_s": t0, "first_alert_s": hit.t_s.iloc[0] if len(hit) else np.nan,
                    "delay_s": (hit.t_s.iloc[0] - t0) if len(hit) else np.nan})
    return pd.DataFrame(out)
