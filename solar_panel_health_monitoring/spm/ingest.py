"""Telemetry ingestion.

File -> (optional long-to-wide pivot) -> the model package's OWN schema detector and canonicaliser
(`cubesat_knn.schema_detector.detect_schema`, `cubesat_knn.data_loader.to_canonical`) -> validation report.

Nothing here scores data. Nothing is guessed: ambiguous units or unrecognised columns are reported, and the caller
must supply an explicit choice (units) or a column mapping. Uploaded files are never modified.
"""
from __future__ import annotations
import re, zipfile
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np, pandas as pd
from cubesat_knn.schema_detector import detect_schema, SchemaError, Schema
from cubesat_knn.data_loader import to_canonical
from cubesat_knn.features import FeatureSchemaError, channels_in
from . import settings
from .model_service import ModelService, IncompatibleBaselineError

TEST_SHEET_RE = re.compile(r"^test")        # same rule as cubesat_knn.data_loader.load_birds_style_workbook


class IngestError(ValueError):
    """User-facing problem with an upload. `code` is stable for the UI/tests; `status` is the HTTP status to use."""
    def __init__(self, code: str, message: str, details=None, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details, status


def msg(level: str, code: str, text: str) -> dict:
    return {"level": level, "code": code, "text": text}


# --------------------------------------------------------------------------------------------------------- file level
def check_file(path: Path, ext: str) -> None:
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise IngestError("unsupported_type", f"Unsupported file type {ext or '(none)'}. Upload a .csv or .xlsx file "
                          "(.xls is not supported; save it as .xlsx first).", status=415)
    if path.stat().st_size == 0:
        raise IngestError("empty_file", "The file is empty.")
    head = path.read_bytes()[:4096] if ext == ".csv" else path.open("rb").read(4)
    if ext == ".csv":
        if b"\x00" in head:
            raise IngestError("not_text", "This does not look like a CSV text file (binary content found).", status=415)
    else:
        if head[:2] != b"PK":
            raise IngestError("not_xlsx", "This is not a valid .xlsx workbook (it is not a zip container).", status=415)
        try:
            with zipfile.ZipFile(path) as z:
                total = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile as e:
            raise IngestError("corrupt_xlsx", "The workbook is corrupted and could not be opened.", status=415) from e
        if total > settings.MAX_XLSX_UNCOMPRESSED_MB * 1024 * 1024:
            raise IngestError("xlsx_too_large", f"The workbook expands to more than {settings.MAX_XLSX_UNCOMPRESSED_MB} MB "
                              "uncompressed, which is above this service's limit.", status=413)


def _sniff_sep(path: Path) -> str:
    with path.open("rb") as f:
        line = f.readline(65536).decode("utf-8-sig", errors="replace")
    counts = {s: line.count(s) for s in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def read_csv_frame(path: Path) -> pd.DataFrame:
    sep = _sniff_sep(path)
    last = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=object, sep=sep, encoding=enc)       # same call as cubesat_knn.load_flat_csv
            break
        except UnicodeDecodeError as e:
            last = e
        except pd.errors.EmptyDataError as e:
            raise IngestError("empty_file", "The file has no rows.") from e
        except pd.errors.ParserError as e:
            raise IngestError("parse_error", f"The CSV could not be parsed: {e}") from e
    else:
        raise IngestError("encoding", f"The CSV text encoding is not supported: {last}")
    if df.empty:
        raise IngestError("empty_file", "The file has a header but no data rows.")
    return df


def open_workbook(path: Path) -> pd.ExcelFile:
    try:
        return pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:                                                   # openpyxl raises many types
        raise IngestError("corrupt_xlsx", f"The workbook could not be read: {type(e).__name__}.", status=415) from e


# --------------------------------------------------------------------------------------------------------- long format
_PANEL_COLS = {"panel_id", "panel", "channel", "panel_name", "panel_no", "panel_number"}
_V_RE = re.compile(r"^\s*(?:panel[_ ]?)?(?:voltage|volt|v)\s*(?:[_\s]*[\(\[]?\s*(mv|v|volts?|millivolts?)\s*[\)\]]?)?\s*$", re.I)
_I_RE = re.compile(r"^\s*(?:panel[_ ]?)?(?:current|curr|i)\s*(?:[_\s]*[\(\[]?\s*(ma|a|amps?|amperes?|milliamps?|milliamperes?)\s*[\)\]]?)?\s*$", re.I)
_T_ISO = re.compile(r"^\s*(timestamp_utc|timestamp|datetime|date_time)\s*$", re.I)
_T_NUM = re.compile(r"^\s*(elapsed_seconds|elapsed_s|time ?stamp|time_s|t_s|seconds|time\s*\(s\)|time_ms|elapsed_ms|time_min|minutes|time|t|elapsed)\s*$", re.I)
_T_UNITS = {"elapsed_seconds": "s", "elapsed_s": "s", "time stamp": "s", "timestamp": "s", "time_s": "s", "t_s": "s", "seconds": "s",
            "time (s)": "s", "time(s)": "s", "time_ms": "ms", "elapsed_ms": "ms", "time_min": "min", "minutes": "min"}
_T_FACTORS = {"s": 1.0, "ms": 1e-3, "min": 60.0, "h": 3600.0}


def _norm_v(u): return None if u is None else ("mV" if u.lower() in ("mv", "millivolt", "millivolts") else "V")
def _norm_i(u): return None if u is None else ("mA" if u.lower() in ("ma", "milliamp", "milliamps", "milliampere", "milliamperes") else "A")


def detect_long_format(columns) -> dict | None:
    cols = [str(c) for c in columns]
    panel = [c for c in cols if c.strip().lower() in _PANEL_COLS]
    v = [(c, _norm_v(m.group(1))) for c in cols if (m := _V_RE.match(c))]
    i = [(c, _norm_i(m.group(1))) for c in cols if (m := _I_RE.match(c))]
    t_iso = [c for c in cols if _T_ISO.match(c)]
    t_num = [c for c in cols if _T_NUM.match(c) and c not in t_iso]
    if len(panel) != 1 or len(v) != 1 or len(i) != 1 or len(t_iso) + len(t_num) != 1:
        return None
    if t_iso:
        tcol, tkind, tunit = t_iso[0], "iso", None
    else:
        tcol, tkind = t_num[0], "numeric"; tunit = _T_UNITS.get(tcol.strip().lower())
    return {"panel": panel[0], "time": tcol, "time_kind": tkind, "time_unit": tunit,
            "v": v[0][0], "v_unit": v[0][1], "i": i[0][0], "i_unit": i[0][1]}


def _safe_channel(label: str, used: set) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", label) or "P"
    if not s[0].isalpha():
        s = "P" + s
    base, n = s, 2
    while s.lower() in used:
        s = f"{base}{n}"; n += 1
    used.add(s.lower())
    return s


def long_to_wide(df: pd.DataFrame, spec: dict, units: dict) -> tuple[pd.DataFrame, dict, list[str]]:
    """One row per (time, panel) -> one row per time with <PANEL>_voltage_<unit> / <PANEL>_current_<unit> columns.
    Values are moved, never changed, and duplicate (time, panel) observations are rejected instead of averaged."""
    notes: list[str] = []
    d = df[[spec["time"], spec["panel"], spec["v"], spec["i"]]].copy()
    n0 = len(d)
    no_panel = d[spec["panel"]].isna() | (d[spec["panel"]].astype(str).str.strip() == "")
    if no_panel.any():
        raise IngestError("missing_panel_id", f"{int(no_panel.sum())} of {n0} rows have no panel identifier; fill or remove them.")
    d["_p"] = d[spec["panel"]].astype(str).str.strip()
    if spec["time_kind"] == "numeric":
        key = pd.to_numeric(d[spec["time"]], errors="coerce")
        factor = _T_FACTORS[units["time"]]
        key = key.astype(float) * factor; tname = "elapsed_seconds"
    else:
        key = pd.to_datetime(d[spec["time"]], errors="coerce", utc=True); tname = "timestamp_utc"
    bad = int(key.isna().sum())
    if bad:
        raise IngestError("invalid_timestamps", f"{bad} rows have a missing or unreadable timestamp and cannot be placed on the time axis.")
    d["_k"] = key
    dup = int(d.duplicated(["_k", "_p"]).sum())
    if dup:
        raise IngestError("duplicate_observations", f"{dup} rows repeat a panel/time pair. Telemetry must have one reading per panel per timestamp.")
    labels = list(dict.fromkeys(d["_p"]))
    used: set = set(); safe = {lab: _safe_channel(lab, used) for lab in labels}
    piv_v = d.pivot(index="_k", columns="_p", values=spec["v"]); piv_i = d.pivot(index="_k", columns="_p", values=spec["i"])
    wide = pd.DataFrame({tname: piv_v.index.map(lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")) if tname == "timestamp_utc" else piv_v.index.values})
    for lab in labels:
        wide[f"{safe[lab]}_voltage_{units['voltage']}"] = piv_v[lab].values
        wide[f"{safe[lab]}_current_{units['current']}"] = piv_i[lab].values
    wide = wide.astype(object)
    notes.append(f"Long-format file ({n0} rows) pivoted to {len(wide)} timestamps x {len(labels)} panels; values were not altered. "
                 f"Units used: voltage {units['voltage']}, current {units['current']}"
                 + (f", time {units['time']}." if spec['time_kind'] == 'numeric' else ", ISO timestamps normalised to UTC."))
    return wide, {v: k for k, v in safe.items()}, notes


# --------------------------------------------------------------------------------------------------------- preparing a frame
@dataclass
class Prepared:
    frame: pd.DataFrame | None
    schema: Schema | None
    notes: list[str] = field(default_factory=list)
    label_map: dict = field(default_factory=dict)        # internal channel name -> label shown to the user
    long_format: bool = False
    needs_input: list[dict] = field(default_factory=list)


def prepare_frame(df: pd.DataFrame, mapping: dict | None, units: dict | None) -> Prepared:
    units = {k: v for k, v in (units or {}).items() if v}
    try:
        schema = detect_schema(df.columns, mapping)
        return Prepared(df, schema)
    except SchemaError as e:
        first_error = e
    spec = None if mapping else detect_long_format(df.columns)
    if spec is None:
        cols = [str(c) for c in df.columns][:40]
        raise IngestError("schema", f"{first_error}. Recognised column styles: BIRDS workbooks ('Time stamp', 'Vpy (mV)', 'Ipy (mA)'), "
                          "flat files ('elapsed_seconds', 'P1_voltage_V', 'P1_current_A'), or long format with panel_id/time/voltage/current "
                          "columns. For other headers, provide a column mapping.", details={"columns": cols})
    need = []
    if spec["v_unit"] is None and "voltage" not in units:
        need.append({"field": "voltage", "label": f"Unit of '{spec['v']}'", "options": ["V", "mV"]})
    if spec["i_unit"] is None and "current" not in units:
        need.append({"field": "current", "label": f"Unit of '{spec['i']}'", "options": ["A", "mA"]})
    if spec["time_kind"] == "numeric" and spec["time_unit"] is None and "time" not in units:
        need.append({"field": "time", "label": f"Unit of time column '{spec['time']}'", "options": ["s", "ms", "min", "h"]})
    if need:
        return Prepared(None, None, needs_input=need, long_format=True)
    use = {"voltage": spec["v_unit"] or units["voltage"], "current": spec["i_unit"] or units["current"],
           "time": spec["time_unit"] or units.get("time", "s")}
    for k, opts in (("voltage", ("V", "mV")), ("current", ("A", "mA")), ("time", tuple(_T_FACTORS))):
        if use[k] not in opts:
            raise IngestError("bad_units", f"Unsupported {k} unit {use[k]!r}; choose one of {', '.join(opts)}.")
    wide, label_map, notes = long_to_wide(df, spec, use)
    return Prepared(wide, detect_schema(wide.columns, None), notes, label_map, True)


# --------------------------------------------------------------------------------------------------------- reports
def _unit_notes(schema: Schema) -> list[str]:
    out = []
    for c in schema.columns:
        if c.role == "measure" and not c.is_battery and c.measure in ("voltage", "current"):
            target = "mV" if c.measure == "voltage" else "mA"
            out.append(f"{c.name}: {c.unit} -> {target}" + (f" (x{c.factor:g})" if c.factor != 1 else " (no conversion)"))
    return out


def _cadence(t: np.ndarray) -> dict:
    t = t[np.isfinite(t)]
    d = np.diff(t)
    pos = d[d > 0]
    med = float(np.median(pos)) if len(pos) else None
    return {"median_s": med, "min_s": float(pos.min()) if len(pos) else None, "max_s": float(pos.max()) if len(pos) else None,
            "gaps_over_1_5x_median": int((pos > 1.5 * med).sum()) if med else 0,
            "duplicate_timestamps": int(pd.Series(t).duplicated().sum()), "non_monotonic_steps": int((d < 0).sum()),
            "start_s": float(t.min()) if len(t) else None, "end_s": float(t.max()) if len(t) else None}


def quality_report(prep: Prepared, canon: pd.DataFrame) -> tuple[dict, list[dict]]:
    df, schema = prep.frame, prep.schema
    msgs: list[dict] = []
    t = canon["t_s"].to_numpy(float)
    cad = _cadence(t)
    q = {"observations": int(len(canon)), "padding_rows_dropped": int(canon.attrs.get("padding_rows_dropped", 0)),
         "raw_missing_rows_nulled": int(canon.attrs.get("raw_missing_rows_nulled", 0)),
         "timestamps_imputed": int(canon["t_imputed"].sum()), "timestamps_missing": int(np.isnan(t).sum()), "cadence": cad}
    if q["observations"] < 30:
        msgs.append(msg("warning", "few_rows", f"Only {q['observations']} observations; panel statistics will not be meaningful."))
    if cad["duplicate_timestamps"]:
        msgs.append(msg("warning", "duplicate_timestamps", f"{cad['duplicate_timestamps']} duplicate timestamps. All rows are kept and scored."))
    if cad["non_monotonic_steps"]:
        msgs.append(msg("warning", "non_monotonic", f"{cad['non_monotonic_steps']} timestamps go backwards in file order. Rows are ordered by time for the chart."))
    if cad["gaps_over_1_5x_median"]:
        msgs.append(msg("info", "gaps", f"{cad['gaps_over_1_5x_median']} gaps longer than 1.5x the median cadence ({cad['median_s']:g} s). The chart breaks lines at large gaps."))
    if q["timestamps_imputed"]:
        msgs.append(msg("warning", "imputed_time", f"{q['timestamps_imputed']} rows had no timestamp; times were extrapolated at the median cadence (flagged in the results)."))
    if q["timestamps_missing"]:
        msgs.append(msg("warning", "missing_time", f"{q['timestamps_missing']} rows have no usable timestamp; they are scored but cannot be drawn on the chart."))
    if q["padding_rows_dropped"]:
        msgs.append(msg("info", "padding", f"{q['padding_rows_dropped']} spreadsheet filler rows (blank time and raw words) were ignored."))
    if q["raw_missing_rows_nulled"]:
        msgs.append(msg("info", "raw_missing", f"{q['raw_missing_rows_nulled']} rows have no raw telemetry words; their calibrated values were treated as missing."))
    panels = []
    for ch in channels_in(canon):
        v = canon[f"V:{ch}"].to_numpy(float)
        i = canon[f"I:{ch}"].to_numpy(float) if f"I:{ch}" in canon else np.full(len(v), np.nan)
        raw_v = df[next(c.name for c in schema.columns if c.role == "measure" and c.channel == ch and c.measure == "voltage")]
        raw_i = df[next(c.name for c in schema.columns if c.role == "measure" and c.channel == ch and c.measure == "current")] if f"I:{ch}" in canon else None
        nn_v = int((pd.to_numeric(raw_v, errors="coerce").isna() & raw_v.notna()).sum())
        nn_i = int((pd.to_numeric(raw_i, errors="coerce").isna() & raw_i.notna()).sum()) if raw_i is not None else 0
        p = {"channel": ch, "panel": prep.label_map.get(ch, ch), "samples": int(len(v)), "missing_v": int(np.isnan(v).sum()),
             "missing_i": int(np.isnan(i).sum()), "nonnumeric_v": nn_v, "nonnumeric_i": nn_i,
             "negative_v": int((v < 0).sum()), "negative_i": int((i < 0).sum()), "zero_v": int((v == 0).sum()),
             "constant_v": bool(np.nanmin(v) == np.nanmax(v)) if np.isfinite(v).any() else False,
             "v_min_V": float(np.nanmin(v) / 1000) if np.isfinite(v).any() else None, "v_max_V": float(np.nanmax(v) / 1000) if np.isfinite(v).any() else None,
             "i_max_mA": float(np.nanmax(i)) if np.isfinite(i).any() else None}
        panels.append(p)
        lab = p["panel"]
        if p["missing_v"] == p["samples"]:
            msgs.append(msg("error", "no_voltage", f"Panel {lab}: no usable voltage readings."))
        elif p["missing_v"]:
            msgs.append(msg("warning", "missing_values", f"Panel {lab}: {p['missing_v']} missing voltage readings (not scored, never filled with zero)."))
        if p["nonnumeric_v"] or p["nonnumeric_i"]:
            msgs.append(msg("warning", "nonnumeric", f"Panel {lab}: {p['nonnumeric_v']} voltage and {p['nonnumeric_i']} current cells are not numbers and were treated as missing."))
        if p["negative_v"] or p["negative_i"]:
            msgs.append(msg("warning", "negative", f"Panel {lab}: {p['negative_v']} negative voltage and {p['negative_i']} negative current readings."))
        if p["constant_v"] and p["samples"] > 1:
            msgs.append(msg("warning", "constant", f"Panel {lab}: voltage is constant over the whole file."))
    q["panels"] = panels
    if schema.unmapped:
        msgs.append(msg("info", "unmapped", f"Columns not used by the model: {', '.join(schema.unmapped[:12])}" + (" ..." if len(schema.unmapped) > 12 else "")))
    if schema.battery:
        msgs.append(msg("info", "battery", "Battery columns were found; this model does not use them."))
    if any("temperature" in d for d in schema.channels.values()):
        msgs.append(msg("info", "temperature", "Temperature columns were found; this model does not use them."))
    for w in schema.warnings:
        msgs.append(msg("warning", "schema", w))
    return q, msgs


def build_sheet_report(name: str, prep: Prepared, canon: pd.DataFrame, mode: str, model: ModelService) -> dict:
    q, msgs = quality_report(prep, canon)
    rep = {"name": name, "status": "ok", "mode": mode, "long_format": prep.long_format, "panels": [p["panel"] for p in q["panels"]],
           "channels": [p["channel"] for p in q["panels"]], "rows": q["observations"], "quality": q,
           "unit_conversions": _unit_notes(prep.schema), "time_column": prep.schema.time_col or prep.schema.iso_time_col,
           "scale": [], "messages": msgs + [msg("info", "note", n) for n in prep.notes]}
    if mode == "lab_test":
        rep["messages"].append(msg("info", "lab_test", "Sheet name starts with 'test': treated as a bench/lab-test session (it is still scored)."))
    try:
        F = model.features(canon)
        rep["scale"] = model.check_scale(F)
        model.assert_compatible(F)
    except FeatureSchemaError as e:
        rep["messages"].append(msg("error", "feature_schema", str(e)))
    except IncompatibleBaselineError as e:
        rep["messages"].append(msg("error", "scale_guard", str(e)))
    if any(m["level"] == "error" for m in rep["messages"]):
        rep["status"] = "error"
    return rep


# --------------------------------------------------------------------------------------------------------- orchestration
def _sheet_names(path: Path, ext: str) -> list[str]:
    return [Path(path).stem] if ext == ".csv" else list(open_workbook(path).sheet_names)


def clean_label(s: str, default: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.\-]", "", (s or "").strip())[:n].strip()
    return s or default


def iter_prepared(path: Path, ext: str, original_name: str, wanted: list[str] | None, mapping, units):
    """Yield (name, mode, raw_row_count, Prepared | IngestError) per sheet. Errors are yielded, not raised."""
    if ext == ".csv":
        name = clean_label(Path(original_name).stem, "telemetry")
        try:
            df = read_csv_frame(path)
            yield name, "flight", len(df), prepare_frame(df, mapping, units)
        except IngestError as e:
            yield name, "flight", 0, e
        return
    xl = open_workbook(path)
    names = xl.sheet_names
    if len(names) > settings.MAX_SHEETS:
        raise IngestError("too_many_sheets", f"The workbook has {len(names)} sheets; the limit is {settings.MAX_SHEETS}.", status=413)
    for sheet in names:
        if wanted is not None and sheet not in wanted:
            continue
        mode = "lab_test" if TEST_SHEET_RE.match(sheet.strip().lower()) else "flight"
        try:
            df = xl.parse(sheet, dtype=object, header=0)
            if df.empty:
                raise IngestError("empty_sheet", "The sheet has no data rows.")
            yield sheet, mode, len(df), prepare_frame(df, mapping, units)
        except IngestError as e:
            yield sheet, mode, 0, e
        except Exception as e:
            yield sheet, mode, 0, IngestError("sheet_unreadable", f"The sheet could not be read: {type(e).__name__}.")


def inspect_file(path: Path, ext: str, original_name: str, model: ModelService, satellite: str, mapping=None, units=None) -> dict:
    check_file(path, ext)
    sheets = []
    for name, mode, nrows, prep in iter_prepared(path, ext, original_name, None, mapping, units):
        if isinstance(prep, IngestError):
            sheets.append({"name": name, "status": "error", "selectable": False, "mode": mode, "rows": nrows, "panels": [],
                           "messages": [msg("error", prep.code, prep.message)], "details": prep.details, "needs_input": []})
            continue
        if prep.needs_input:
            sheets.append({"name": name, "status": "needs_input", "selectable": False, "mode": mode, "rows": nrows, "panels": [],
                           "messages": [msg("warning", "needs_units", "Units are not stated in the column names. Choose them below; they are never guessed.")],
                           "needs_input": prep.needs_input})
            continue
        try:
            canon = to_canonical(prep.frame, prep.schema, satellite, name, None, mode)
            if len(canon) == 0:
                raise IngestError("no_rows", "No telemetry rows remain after removing empty rows.")
            rep = build_sheet_report(name, prep, canon, mode, model)
        except IngestError as e:
            rep = {"name": name, "status": "error", "mode": mode, "rows": nrows, "panels": [], "messages": [msg("error", e.code, e.message)]}
        except Exception as e:                                               # a sheet we cannot interpret must not hide the others
            rep = {"name": name, "status": "error", "mode": mode, "rows": nrows, "panels": [],
                   "messages": [msg("error", "unreadable", f"The sheet could not be interpreted ({type(e).__name__}).")]}
        rep["selectable"] = rep["status"] == "ok"; rep.setdefault("needs_input", [])
        sheets.append(rep)
    return {"file_type": ext.lstrip("."), "sheet_count": len(sheets), "sheets": sheets,
            "usable_sheets": [s["name"] for s in sheets if s["selectable"]]}


def load_tables(path: Path, ext: str, original_name: str, sheets: list[str], satellite: str, mapping=None, units=None) -> list[tuple[str, pd.DataFrame, dict]]:
    """Canonical tables for the chosen sheets. Raises IngestError if any chosen sheet cannot be loaded."""
    check_file(path, ext)
    out, errors = [], []
    found = set()
    for name, mode, nrows, prep in iter_prepared(path, ext, original_name, sheets if ext != ".csv" else None, mapping, units):
        found.add(name)
        if isinstance(prep, IngestError):
            errors.append({"sheet": name, "problem": prep.message}); continue
        if prep.needs_input:
            errors.append({"sheet": name, "problem": "units must be chosen before analysis"}); continue
        canon = to_canonical(prep.frame, prep.schema, satellite, name, None, mode)
        if len(canon) == 0:
            errors.append({"sheet": name, "problem": "no telemetry rows"}); continue
        out.append((name, canon, {"label_map": prep.label_map, "long_format": prep.long_format,
                                  "iso_col": prep.schema.iso_time_col, "mode": mode}))
    missing = [s for s in sheets if ext != ".csv" and s not in found]
    errors += [{"sheet": s, "problem": "sheet not found in the workbook"} for s in missing]
    if errors:
        raise IngestError("sheet_errors", "Some selected sheets cannot be analysed: " + "; ".join(f"{e['sheet']}: {e['problem']}" for e in errors[:5]), details=errors)
    total = sum(len(t) for _, t, _ in out)
    if total > settings.MAX_OBSERVATIONS:
        raise IngestError("too_many_rows", f"{total:,} rows selected; the limit is {settings.MAX_OBSERVATIONS:,} per analysis. Select fewer sheets or split the file.", status=413)
    return out
