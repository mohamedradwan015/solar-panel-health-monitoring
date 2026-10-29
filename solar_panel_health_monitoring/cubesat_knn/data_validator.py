"""Data-quality audit. Reports problems; never deletes observations."""
from __future__ import annotations
import numpy as np, pandas as pd


def hex_to_int(series: pd.Series) -> pd.Series:
    def f(x):
        if x is None or (isinstance(x, float) and np.isnan(x)): return np.nan
        try: return int(str(x).strip(), 16)
        except ValueError: return np.nan
    return series.map(f)


def audit_sheet(raw: pd.DataFrame, schema, satellite: str, sheet: str, file: str) -> tuple[dict, list[dict]]:
    """Return (sheet-level row, column-level rows)."""
    n_raw = len(raw)
    nonempty = raw.dropna(how="all")
    n = len(nonempty)
    t = pd.to_numeric(nonempty[schema.time_col], errors="coerce") if schema.time_col else pd.Series(dtype=float)
    dts = np.diff(t.dropna().values) if len(t.dropna()) > 1 else np.array([])
    med = float(np.median(dts)) if len(dts) else np.nan
    row = {"file": file, "satellite": satellite, "sheet": sheet,
           "rows_raw": n_raw, "rows_nonempty": n, "cols": raw.shape[1],
           "time_col": schema.time_col, "channels": ",".join(schema.channels), "n_channels": len(schema.channels),
           "battery_cols": ",".join(schema.battery.values()), "unmapped_cols": ";".join(schema.unmapped),
           "duplicate_rows": int(nonempty.duplicated().sum()),
           "duplicate_timestamps": int(t.dropna().duplicated().sum()) if len(t) else np.nan,
           "non_monotonic_steps": int((dts <= 0).sum()) if len(dts) else 0,
           "dt_median_s": med, "dt_min_s": float(dts.min()) if len(dts) else np.nan,
           "dt_max_s": float(dts.max()) if len(dts) else np.nan,
           "gaps_gt_1p5x_median": int((dts > 1.5 * med).sum()) if len(dts) and med > 0 else 0}
    crow, susp = [], 0
    for c in schema.columns:
        s = nonempty[c.name]
        rec = {"file": file, "satellite": satellite, "sheet": sheet, "column": c.name, "role": c.role,
               "measure": c.measure, "channel": c.channel, "unit": c.unit}
        if c.role in ("measure", "time"):
            num = pd.to_numeric(s, errors="coerce")
            rec.update(n=n, missing=int(s.isna().sum()), nonnumeric=int((num.isna() & s.notna()).sum()),
                       infinite=int(np.isinf(num.astype(float)).sum()), min=num.min(), max=num.max(),
                       mean=num.mean(), std=num.std(), n_unique=int(num.nunique()),
                       constant=bool(num.nunique(dropna=True) <= 1),
                       n_zero=int((num == 0).sum()), n_negative=int((num < 0).sum()))
            if c.role == "measure" and c.measure in ("voltage", "current") and not c.is_battery:  # battery current is legitimately negative when charging
                susp += int((num < 0).sum())
        elif c.role == "raw":
            h = hex_to_int(s)
            rec.update(n=n, missing=int(s.isna().sum()), nonnumeric=int((h.isna() & s.notna()).sum()),
                       min=h.min(), max=h.max(), n_unique=int(h.nunique()))
        else:
            rec.update(n=n, missing=int(s.isna().sum()))
        crow.append(rec)
    row["negative_panel_V_or_I_values"] = susp
    rawc = list(schema.raw_cols.values())
    if rawc and schema.time_col:
        pad = t.isna() & nonempty[rawc].isna().all(axis=1)
        row["padding_rows(blank t & raw, calibrated zeros)"] = int(pad.sum())
        row["untimed_rows_with_data"] = int((t.isna() & ~pad).sum())
    return row, crow


def raw_vs_calibrated(raw: pd.DataFrame, schema) -> list[dict]:
    """Check calibrated = a*raw + b for each measurement column that has a raw hex twin."""
    out = []
    for cal, rawcol in schema.raw_cols.items():
        h = hex_to_int(raw[rawcol]); y = pd.to_numeric(raw[cal], errors="coerce")
        m = h.notna() & y.notna()
        if m.sum() < 10 or h[m].nunique() < 3:
            out.append({"column": cal, "raw_column": rawcol, "n": int(m.sum()), "slope": np.nan, "intercept": np.nan,
                        "r2": np.nan, "max_abs_resid": np.nan, "note": "too few distinct raw values"}); continue
        a, b = np.polyfit(h[m].astype(float), y[m].astype(float), 1)
        res = y[m] - (a * h[m] + b)
        ss_tot = float(((y[m] - y[m].mean()) ** 2).sum())
        r2 = 1 - float((res ** 2).sum()) / ss_tot if ss_tot > 0 else np.nan
        out.append({"column": cal, "raw_column": rawcol, "n": int(m.sum()), "slope": a, "intercept": b,
                    "r2": r2, "max_abs_resid": float(res.abs().max()), "note": ""})
    return out
