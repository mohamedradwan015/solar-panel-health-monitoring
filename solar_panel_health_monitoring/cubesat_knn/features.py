"""Feature construction for a variable number of panel channels.

Channel-level features are *per (timestamp, channel)* and depend on siblings only through order-free statistics
(median / max over the other channels), so the feature dimension is independent of how many channels exist.
All rolling statistics are causal (past samples only). Satellite-level features aggregate channel features with
min / median across channels -> also fixed dimension.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from .config import DetectorConfig, RuleLabelConfig

KEYS = ["satellite", "session", "source_row", "channel"]


class FeatureSchemaError(ValueError):
    pass


def channels_in(table: pd.DataFrame) -> list[str]:
    return sorted(c[2:] for c in table.columns if c.startswith("V:"))


def feature_names(cfg: DetectorConfig) -> list[str]:
    if cfg.level == "satellite":
        f = ["sun_V", "lit_ratio_min", "lit_ratio_med", "i_lit_ratio_min", "i_lit_ratio_med",
             "v_rel_max_min", "v_range_min", "v_range_med"]
        if cfg.use_battery: f += ["Vbat", "Ibat"]
        return f
    f = ["V_V", "I_mA"]
    if cfg.feature_set == "rel":
        f += ["sun_V", "lit_ratio", "i_lit_ratio", "v_rel_med", "v_rel_max", "v_range_V", "dV_V"]
    elif cfg.feature_set == "hyb1":     # absolute V, I + common-mode illumination context only (no sibling ratios)
        f += ["sun_V"]
    elif cfg.feature_set == "hyb2":     # absolute V, I + illumination context + the two persistence ratios
        f += ["sun_V", "lit_ratio", "i_lit_ratio"]
    elif cfg.feature_set != "abs":
        raise FeatureSchemaError(f"unknown feature_set {cfg.feature_set!r}")
    if cfg.use_temperature: f += ["T_C"] + (["T_dev_C"] if cfg.feature_set in ("rel", "hyb2") else [])
    if cfg.use_battery: f += ["Vbat", "Ibat"]
    return f


def _sib(M: np.ndarray, j: int, fn):
    """Statistic over all columns except j, ignoring NaN. Empty -> NaN."""
    other = np.delete(M, j, axis=1)
    if other.shape[1] == 0: return np.full(M.shape[0], np.nan)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return fn(other, axis=1)


def _roll(x: np.ndarray, w: int, q: float | None = None, how: str = "q"):
    s = pd.Series(x).rolling(w, min_periods=3)
    if how == "q": return s.quantile(q).values
    if how == "range": return (s.max() - s.min()).values


def build_channel_features(table: pd.DataFrame, cfg: DetectorConfig, require_channels: int | None = None) -> pd.DataFrame:
    """Long frame, one row per (observation, channel). Raises FeatureSchemaError if schema can't support cfg."""
    chans = channels_in(table)
    if not chans: raise FeatureSchemaError("table has no voltage channels ('V:<ch>' columns)")
    if cfg.feature_set in ("rel", "hyb2") and len(chans) < 2:
        raise FeatureSchemaError("feature_set='rel' needs >=2 channels for sibling statistics; use feature_set='abs' "
                                 "or calibrate a baseline for this satellite")
    if any(f"I:{c}" not in table.columns for c in chans):
        raise FeatureSchemaError("every channel needs a current column ('I:<ch>')")
    if cfg.use_temperature and any(f"T:{c}" not in table.columns for c in chans):
        raise FeatureSchemaError("use_temperature=True but temperature columns missing")
    if cfg.use_battery and not {"Vbat", "Ibat"} <= set(table.columns):
        raise FeatureSchemaError("use_battery=True but battery columns missing")
    out = []
    for (sat, ses), g in table.groupby(["satellite", "session"], sort=False):
        g = g.sort_values(["t_s", "source_row"], kind="stable")
        t = g["t_s"].values.astype(float)
        dts = np.diff(t[~np.isnan(t)])
        dt = float(np.median(dts[dts > 0])) if (dts > 0).any() else 1.0
        w = int(max(3, round(cfg.window_seconds / dt)))
        V = np.column_stack([g[f"V:{c}"].values for c in chans]).astype(float)
        I = np.column_stack([g[f"I:{c}"].values for c in chans]).astype(float)
        Vq = np.column_stack([_roll(V[:, j], w, 0.9) for j in range(len(chans))])
        Iq = np.column_stack([_roll(I[:, j], w, 0.9) for j in range(len(chans))])
        Vr = np.column_stack([_roll(V[:, j], w, how="range") for j in range(len(chans))])
        sun = np.nanmax(V, axis=1) if V.shape[1] else np.full(len(g), np.nan)
        if np.isnan(V).all(axis=1).any(): sun = np.where(np.isnan(V).all(axis=1), np.nan, sun)
        soft = cfg.current_softening_mA
        for j, c in enumerate(chans):
            d = pd.DataFrame({"satellite": sat, "session": ses, "source_row": g["source_row"].values, "channel": c,
                              "t_s": t, "t_imputed": g["t_imputed"].values, "mode": g["mode"].values,
                              "V_mV": V[:, j], "I_mA": I[:, j]})
            d["V_V"] = V[:, j] / 1000.0; d["sun_V"] = sun / 1000.0
            if len(chans) >= 2:
                sib_q = _sib(Vq, j, np.nanmax); sib_med = _sib(V, j, np.nanmedian); sib_max = _sib(V, j, np.nanmax)
                sib_iq = _sib(Iq, j, np.nanmax)
                d["lit_ratio"] = Vq[:, j] / np.where(sib_q > 0, sib_q, np.nan)
                d["i_lit_ratio"] = (Iq[:, j] + soft) / (sib_iq + soft)
                d["sib_i_q90_mA"] = sib_iq          # used only to gate severity estimates (illumination sufficient?)
                d["v_rel_med"] = V[:, j] / np.where(sib_med > 0, sib_med, np.nan)
                d["v_rel_max"] = V[:, j] / np.where(sib_max > 0, sib_max, np.nan)
            d["v_range_V"] = Vr[:, j] / 1000.0
            dV = np.abs(np.diff(V[:, j], prepend=np.nan)) / 1000.0
            d["dV_V"] = dV
            if cfg.use_temperature:
                T = np.column_stack([g[f"T:{cc}"].values for cc in chans]).astype(float)
                d["T_C"] = T[:, j]
                if len(chans) >= 2: d["T_dev_C"] = T[:, j] - _sib(T, j, np.nanmedian)
            if cfg.use_battery:
                d["Vbat"] = g["Vbat"].values; d["Ibat"] = g["Ibat"].values
            d["all_channels_zero_V"] = (np.nan_to_num(V, nan=1.0) == 0).all(axis=1)
            out.append(d)
    F = pd.concat(out, ignore_index=True)
    return F


def build_satellite_features(ch_feats: pd.DataFrame, table: pd.DataFrame, cfg: DetectorConfig) -> pd.DataFrame:
    """Aggregate channel features per observation (min/median over channels). Needs 'rel' channel features."""
    if "lit_ratio" not in ch_feats: raise FeatureSchemaError("satellite level needs rel channel features (>=2 channels)")
    k = ["satellite", "session", "source_row"]
    gb = ch_feats.groupby(k, sort=False)
    S = gb.agg(t_s=("t_s", "first"), t_imputed=("t_imputed", "first"), mode=("mode", "first"),
               sun_V=("sun_V", "first"), lit_ratio_min=("lit_ratio", "min"), lit_ratio_med=("lit_ratio", "median"),
               i_lit_ratio_min=("i_lit_ratio", "min"), i_lit_ratio_med=("i_lit_ratio", "median"),
               v_rel_max_min=("v_rel_max", "min"), v_range_min=("v_range_V", "min"), v_range_med=("v_range_V", "median"),
               all_channels_zero_V=("all_channels_zero_V", "first"),
               worst_channel=("lit_ratio", lambda s: ch_feats.loc[s.idxmin(), "channel"] if s.notna().any() else None)
               ).reset_index()
    if cfg.use_battery:
        S = S.merge(table[k + ["Vbat", "Ibat"]], on=k, how="left")
    S["channel"] = "ALL"
    return S


def rule_labels(table: pd.DataFrame, rc: RuleLabelConfig | None = None) -> pd.DataFrame:
    """Reproduce the published rules (Jara Cespedes et al. 2022, Eq.1-2). RULE-DERIVED, NOT VERIFIED.
    type1: V < 1200 mV.  type2: V(t-1), V(t), V(t+1) all in [2000,3000] mV and I(t) < 50 mA.
    Note: type2 uses the next sample, so these labels are for evaluation only, never for features."""
    rc = rc or RuleLabelConfig()
    res = []
    for (sat, ses), g in table.groupby(["satellite", "session"], sort=False):
        g = g.sort_values(["t_s", "source_row"], kind="stable")
        for c in channels_in(table):
            v = g[f"V:{c}"]; i = g[f"I:{c}"]
            t1 = v < rc.type1_voltage_below_mV
            inb = v.between(rc.type2_voltage_low_mV, rc.type2_voltage_high_mV)
            run = inb & inb.shift(1, fill_value=False) & inb.shift(-1, fill_value=False)
            t2 = run & (i < rc.type2_current_below_mA)
            res.append(pd.DataFrame({"satellite": sat, "session": ses, "source_row": g["source_row"].values,
                                     "channel": c, "rule_type1": t1.values, "rule_type2": t2.values}))
    L = pd.concat(res, ignore_index=True)
    L["rule_anomaly"] = L["rule_type1"] | L["rule_type2"]
    return L
