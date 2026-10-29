"""Load telemetry workbooks/CSVs into one canonical wide table. Raw files are opened read-only."""
from __future__ import annotations
import re, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
from .schema_detector import detect_schema, Schema, SchemaError

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})


def parse_session_date(sheet: str, default_year: int | None):
    """Parse 'March 28', '9 Nov 2020', 'January 02 2022'. Returns (date|None, year_was_assumed)."""
    s = sheet.strip().lower()
    m = re.match(r"^([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?$", s) or None
    if m:
        mon, day, yr = m.group(1), int(m.group(2)), m.group(3)
    else:
        m = re.match(r"^(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?$", s)
        if not m: return None, False
        day, mon, yr = int(m.group(1)), m.group(2), m.group(3)
    if mon not in _MONTHS and mon[:3] not in _MONTHS: return None, False
    month = _MONTHS.get(mon, _MONTHS.get(mon[:3]))
    assumed = yr is None
    year = int(yr) if yr else default_year
    if year is None: return None, True
    try: return dt.date(year, month, day), assumed
    except ValueError: return None, assumed


def read_workbook_raw(path) -> dict:
    """All sheets as object-dtype frames (nothing coerced). Original row number = index + 2."""
    return pd.read_excel(path, sheet_name=None, dtype=object, header=0)


def to_canonical(df: pd.DataFrame, schema: Schema, satellite: str, session: str,
                 session_date=None, mode: str = "flight") -> pd.DataFrame:
    """Return wide canonical frame. Units: panel V in mV, I in mA, T in C; battery V in V.
    Columns: satellite, session, session_date, mode, source_row, t_s, then 'V:<ch>', 'I:<ch>', 'T:<ch>', 'Vbat','Ibat','Tbat'."""
    df = df.copy()
    df["_source_row"] = df.index + 2          # Excel row number (header is row 1)
    df = df.dropna(how="all", subset=[c for c in df.columns if c != "_source_row"])
    # --- padding rows: blank timestamp AND blank raw words, but formula-filled calibrated zeros.
    #     These are spreadsheet filler, not observations; keeping them would fake 'zero output'.
    rawc = list(schema.raw_cols.values())
    t_num = pd.to_numeric(df[schema.time_col], errors="coerce") if schema.time_col else None
    n_pad = 0
    if rawc and t_num is not None:
        pad = t_num.isna() & df[rawc].isna().all(axis=1)
        n_pad = int(pad.sum())
        df, t_num = df.loc[~pad], t_num.loc[~pad]
    out = pd.DataFrame({"satellite": satellite, "session": session, "session_date": session_date,
                        "mode": mode, "source_row": df["_source_row"].values})
    # --- rows that have a timestamp but NO raw telemetry words: calibrated zeros are formula filler, not measurements.
    #     Keep the row (identity/time), set every measurement to missing, flag it.
    out["raw_missing"] = df[rawc].isna().all(axis=1).values if rawc else False
    out["t_imputed"] = False
    if schema.time_col:
        t = pd.to_numeric(df[schema.time_col], errors="coerce").values.astype(float)
        med = np.nanmedian(np.diff(t[~np.isnan(t)])) if (~np.isnan(t)).sum() > 1 else np.nan
        imputed = np.isnan(t)
        if imputed.any() and not np.isnan(med):
            last_i, last_t = None, None
            for i in range(len(t)):          # untimed continuation rows: extrapolate at median cadence (flagged)
                if not np.isnan(t[i]) and not imputed[i]:
                    last_i, last_t = i, t[i]
                elif last_i is not None:
                    t[i] = last_t + (i - last_i) * med
        out["t_s"] = t; out["t_imputed"] = imputed
    else:
        ts = pd.to_datetime(df[schema.iso_time_col], errors="coerce", utc=True)
        out["t_s"] = (ts - ts.min()).dt.total_seconds().values
    if schema.iso_time_col:
        out["timestamp_iso"] = df[schema.iso_time_col].values
    tag = {"voltage": "V", "current": "I", "temperature": "T"}
    for ch, d in schema.channels.items():
        for meas, col in d.items():
            vals = pd.to_numeric(df[col], errors="coerce").values.astype(float) * schema.info(col).factor
            out[f"{tag[meas]}:{ch}"] = np.where(np.isinf(vals), np.nan, vals)   # inf -> missing (documented)
    btag = {"voltage": "Vbat", "current": "Ibat", "temperature": "Tbat"}
    for meas, col in schema.battery.items():
        f = 1.0  # battery voltage stays in V, battery current mA
        out[btag[meas]] = pd.to_numeric(df[col], errors="coerce").values * f
    if rawc and out["raw_missing"].any():
        mcols = [c for c in out.columns if c[:2] in ("V:", "I:", "T:") or c in ("Vbat", "Ibat", "Tbat")]
        out.loc[out["raw_missing"].values, mcols] = np.nan
    for meas, col in schema.context.items():
        out[f"ctx:{meas}"] = pd.to_numeric(df[col], errors="coerce").values
    out = out.reset_index(drop=True)
    out.attrs["padding_rows_dropped"] = n_pad; out.attrs["raw_missing_rows_nulled"] = int(out["raw_missing"].sum())
    return out


def load_birds_style_workbook(path, satellite: str, default_year: int | None = None,
                              mapping: dict | None = None, test_sheet_pattern=r"^test") -> tuple[pd.DataFrame, dict]:
    """Load every worksheet; each sheet = one session. Returns (canonical table, per-sheet info)."""
    raw = read_workbook_raw(path)
    frames, info = [], {}
    for sheet, df in raw.items():
        schema = detect_schema(df.columns, mapping)
        date, assumed = parse_session_date(sheet, default_year)
        mode = "lab_test" if re.match(test_sheet_pattern, sheet.strip().lower()) else "flight"
        can = to_canonical(df, schema, satellite, sheet, date, mode)
        frames.append(can); info[sheet] = {"schema": schema, "date": date, "year_assumed": assumed, "mode": mode,
                                           "raw_shape": df.shape,
                                           "padding_rows_dropped": can.attrs.get("padding_rows_dropped", 0),
                                           "untimed_rows_imputed": int(can["t_imputed"].sum())}
    return pd.concat(frames, ignore_index=True), info


def load_flat_sheet(path, sheet, satellite: str, mapping: dict | None = None):
    df = pd.read_excel(path, sheet_name=sheet, dtype=object)
    schema = detect_schema(df.columns, mapping)
    return to_canonical(df, schema, satellite, sheet, None, "flight"), schema


def load_flat_csv(path, satellite: str, mapping: dict | None = None, session: str | None = None):
    """Single-session CSV (e.g. benchmark style 'P1_voltage_V', 'elapsed_seconds'). Returns (canonical table, schema)."""
    df = pd.read_csv(path, dtype=object)
    if df.empty: raise SchemaError(f"{path}: file has no rows")
    schema = detect_schema(df.columns, mapping)
    return to_canonical(df, schema, satellite, session or Path(path).stem, None, "flight"), schema
