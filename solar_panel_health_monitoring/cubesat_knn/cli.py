"""Command-line entry point.
  python -m cubesat_knn.cli verify  --artifacts DIR
  python -m cubesat_knn.cli predict --artifacts DIR --input FILE(.xlsx|.csv) --satellite NAME [--default-year 2021] [--mapping map.json] --out pred.csv
"""
from __future__ import annotations
import argparse, json, sys, hashlib
from pathlib import Path
import pandas as pd


def _verify(d: Path):
    man = json.loads((d / "manifest.json").read_text()); bad = []
    for n, h in man["files"].items():
        h = h["sha256"] if isinstance(h, dict) else h
        if hashlib.sha256((d / n).read_bytes()).hexdigest() != h: bad.append(n)
    return man, bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cubesat_knn.cli"); sp = ap.add_subparsers(dest="cmd", required=True)
    v = sp.add_parser("verify"); v.add_argument("--artifacts", required=True)
    p = sp.add_parser("predict"); p.add_argument("--artifacts", required=True); p.add_argument("--input", required=True)
    p.add_argument("--satellite", required=True); p.add_argument("--default-year", type=int); p.add_argument("--mapping"); p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from .pipeline import CubeSatKNNPipeline, IncompatibleBaselineError
    from .schema_detector import SchemaError
    from .features import FeatureSchemaError
    d = Path(a.artifacts)
    if a.cmd == "verify":
        man, bad = _verify(d); print(f"feature_set={man.get('feature_set')} files={len(man['files'])} mismatches={bad}"); return 1 if bad else 0
    try:
        pipe = CubeSatKNNPipeline.load_trusted_artifacts(d)
        mapping = json.loads(Path(a.mapping).read_text()) if a.mapping else None
        from .data_loader import load_birds_style_workbook, load_flat_csv
        if a.input.lower().endswith((".xlsx", ".xlsm")):
            table, _ = load_birds_style_workbook(a.input, a.satellite, a.default_year, mapping)
        elif a.input.lower().endswith(".csv"):
            table, _ = load_flat_csv(a.input, a.satellite, mapping)
        else:
            print("ERROR: unsupported input type (use .xlsx or .csv)", file=sys.stderr); return 2
        pred = pipe.predict(table)
    except (IncompatibleBaselineError, FeatureSchemaError, SchemaError, ValueError) as e:
        print(f"UNSCORABLE: {type(e).__name__}: {e}", file=sys.stderr); return 2
    pred.to_csv(a.out, index=False)
    print(f"scored {len(pred)} panel-observations | flagged {int(pred['anomaly'].sum())} ({100*pred['anomaly'].mean():.2f}%) | "
          f"unscorable {int((pred['data_quality_status']=='unscorable').sum())} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
