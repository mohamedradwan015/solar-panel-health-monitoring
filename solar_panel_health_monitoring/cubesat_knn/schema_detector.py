"""Schema discovery: map raw column names to (measurement, channel, unit) without hardcoding panel names.

Supported naming conventions (extensible via an explicit JSON mapping file):
  * BIRDS workbooks : 'Vpy (mV)', 'Ipx (mA)', 'Tmz (°C)', 'Vbat (V)', 'Ibatt(mA)', raw hex 'Vpy (raw)'
  * Benchmark style : 'P1_voltage_V', 'P2_current_A', 'P3_temperature_C'
Anything else is reported as unmapped; it is never silently used.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

BATTERY_ALIASES = {"bat", "batt", "battery"}
_MEASURE = {"V": "voltage", "I": "current", "T": "temperature"}
_UNIT_NORMAL = {"°c": "C", "℃": "C", "c": "C", "mv": "mV", "v": "V", "ma": "mA", "a": "A"}

# canonical units: panel voltage mV, current mA, temperature C ; battery voltage V
_FACTORS = {("voltage", "mV"): 1.0, ("voltage", "V"): 1000.0,
            ("current", "mA"): 1.0, ("current", "A"): 1000.0,
            ("temperature", "C"): 1.0}

RE_BIRDS = re.compile(r"^\s*(?P<m>[VIT])(?P<ch>[A-Za-z][A-Za-z0-9]*)\s*\((?P<u>mV|V|mA|A|°C|℃|C)\)\s*$")
RE_BIRDS_RAW = re.compile(r"^\s*(?P<m>[VIT])(?P<ch>[A-Za-z][A-Za-z0-9]*)\s*\(?\s*raw\s*\)\s*$", re.I)
RE_BENCH = re.compile(r"^\s*(?P<ch>[A-Za-z][A-Za-z0-9]*)_(?P<m>voltage|current|temperature)_(?P<u>mV|V|mA|A|C)\s*$", re.I)
RE_TIME = re.compile(r"^\s*(time\s*stamp|elapsed_seconds|elapsed_s)\s*$", re.I)
RE_ISOTIME = re.compile(r"^\s*(timestamp_utc|timestamp|datetime)\s*$", re.I)
RE_ROWID = re.compile(r"^\s*(sample_id|row_id|index)\s*$", re.I)


class SchemaError(ValueError):
    pass


@dataclass
class ColumnInfo:
    name: str
    role: str                 # time | iso_time | row_id | measure | raw | context | unmapped
    measure: str | None = None
    channel: str | None = None
    unit: str | None = None
    is_battery: bool = False
    factor: float = 1.0       # multiply by this to reach canonical unit


@dataclass
class Schema:
    columns: list[ColumnInfo]
    time_col: str | None
    iso_time_col: str | None
    channels: dict            # channel -> {"voltage": col, "current": col, "temperature": col}
    battery: dict             # {"voltage": col, "current": col, "temperature": col}
    context: dict             # e.g. {"illumination_proxy": col}
    raw_cols: dict            # calibrated col -> raw hex col (cross-check only)
    warnings: list[str] = field(default_factory=list)

    @property
    def unmapped(self):
        return [c.name for c in self.columns if c.role == "unmapped"]

    def signature(self):
        return {"channel_count": len(self.channels),
                "measures_per_channel": sorted({m for v in self.channels.values() for m in v}),
                "has_battery": bool(self.battery)}

    def info(self, name):
        return next(c for c in self.columns if c.name == name)


def _norm_unit(u: str) -> str:
    return _UNIT_NORMAL.get(u.lower(), u)


def detect_schema(column_names, mapping: dict | None = None) -> Schema:
    """mapping (optional): {column_name: {"measure":..,"channel":..,"unit":..,"battery":bool}} or
    {column_name: "time"}. Explicit mapping wins over pattern matching."""
    mapping = mapping or {}
    cols: list[ColumnInfo] = []
    for name in column_names:
        name = str(name)
        if name in mapping:
            m = mapping[name]
            if m == "time":
                cols.append(ColumnInfo(name, "time", unit="s")); continue
            if m == "ignore":
                cols.append(ColumnInfo(name, "unmapped")); continue
            u = _norm_unit(m["unit"]); meas = m["measure"]
            if (meas, u) not in _FACTORS:
                raise SchemaError(f"mapping for {name!r}: unsupported measure/unit {meas}/{u}")
            cols.append(ColumnInfo(name, "measure", meas, str(m["channel"]), u,
                                   bool(m.get("battery", False)), _FACTORS[(meas, u)]))
            continue
        mt = RE_BIRDS.match(name) or RE_BENCH.match(name)
        if mt:
            g = mt.groupdict()
            meas = _MEASURE.get(g["m"], g["m"].lower())
            u = _norm_unit(g["u"]); ch = g["ch"]
            if (meas, u) not in _FACTORS:
                cols.append(ColumnInfo(name, "unmapped")); continue
            bat = ch.lower() in BATTERY_ALIASES
            cols.append(ColumnInfo(name, "measure", meas, "battery" if bat else ch, u, bat, _FACTORS[(meas, u)]))
        elif RE_BIRDS_RAW.match(name):
            g = RE_BIRDS_RAW.match(name).groupdict()
            ch = g["ch"]; bat = ch.lower() in BATTERY_ALIASES
            cols.append(ColumnInfo(name, "raw", _MEASURE[g["m"]], "battery" if bat else ch, "hex", bat))
        elif RE_TIME.match(name):
            cols.append(ColumnInfo(name, "time", unit="s"))
        elif RE_ISOTIME.match(name):
            cols.append(ColumnInfo(name, "iso_time"))
        elif RE_ROWID.match(name):
            cols.append(ColumnInfo(name, "row_id"))
        elif name.lower().startswith("illumination_proxy"):
            cols.append(ColumnInfo(name, "context", "illumination_proxy", None, "unitless"))
        else:
            cols.append(ColumnInfo(name, "unmapped"))

    warnings: list[str] = []
    times = [c.name for c in cols if c.role == "time"]
    if len(times) > 1:
        raise SchemaError(f"ambiguous time columns: {times}; supply an explicit mapping")
    isot = [c.name for c in cols if c.role == "iso_time"]
    channels: dict = {}; battery: dict = {}; context: dict = {}; raw_cols: dict = {}
    for c in cols:
        if c.role == "measure":
            tgt = battery if c.is_battery else channels.setdefault(c.channel, {})
            if c.measure in tgt:
                raise SchemaError(f"duplicate {c.measure} column for channel {c.channel}: {tgt[c.measure]!r}, {c.name!r}")
            tgt[c.measure] = c.name
        elif c.role == "context":
            context[c.measure] = c.name
    for c in cols:
        if c.role == "raw":
            tgt = battery if c.is_battery else channels.get(c.channel, {})
            cal = tgt.get(c.measure)
            if cal: raw_cols[cal] = c.name
    if not times and not isot:
        raise SchemaError("no time column recognised")
    # channels must have voltage to be usable as panel channels
    for ch in list(channels):
        if "voltage" not in channels[ch]:
            warnings.append(f"channel {ch!r} has no voltage column -> excluded from panel channels")
            del channels[ch]
    if not channels:
        raise SchemaError("no panel channels with a voltage column were found")
    for ch, d in channels.items():
        if "current" not in d:
            warnings.append(f"channel {ch!r} has no current column (current features unavailable)")
    return Schema(cols, times[0] if times else None, isot[0] if isot else None,
                  channels, battery, context, raw_cols, warnings)
