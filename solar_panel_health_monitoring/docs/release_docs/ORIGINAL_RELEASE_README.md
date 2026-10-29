# CubeSat solar-panel anomaly detector - final release v1 (package 0.2.0)
**Load this model:** the files in THIS folder (`config.json`, `feature_schema.json`, `model_arrays.npz`, `thresholds.json`, `provenance.json`, `severity_baseline.json`, `manifest.json`). Primary model = feature set **abs**, k=20, 99.5th-percentile threshold. `candidates/` holds comparison models only.
Read first: `FINAL_MODEL_CARD.md` (what it can/cannot do), `FINAL_EVALUATION_REPORT.md`, `ACCEPTANCE_CHECKLIST.md`. Status: **READY FOR SOFTWARE TESTING; scientific/deployment acceptance INCOMPLETE** (see limitations).

## Setup (run all commands from this folder)
```
python -m venv .venv            # Windows: .venv\Scripts\Activate.ps1   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```
## Verify integrity and run tests
```
python -m cubesat_knn.cli verify --artifacts .
python tests/run_tests.py          # fallback runner (expect: 58 passed, 0 failed); or: python -m pytest tests -q
```
## Inference (no training data needed)
```
python -m cubesat_knn.cli predict --artifacts . --input YOUR_FILE.xlsx --satellite NAME --default-year 2021 --out predictions.csv
python -m cubesat_knn.cli predict --artifacts . --input YOUR_FILE.csv  --satellite NAME --out predictions.csv
```
Exit code 0 = scored; 2 = unscorable (schema problem or scale guard refusal; message printed to stderr, no output file). Supported inputs: BIRDS-style workbooks (`Time stamp`, `Vpy (mV)`, `Ipy (mA)`, one sheet = one session) and single-session CSVs (`elapsed_seconds`, `P1_voltage_V`, `P1_current_A`). Other column names need `--mapping map.json` (template: `configs/example_schema_mapping.json`); unrecognised columns are reported, never guessed. Python API: `CubeSatKNNPipeline.load_trusted_artifacts(".")`, `.predict(table)`. Output columns: `predictions_schema.md`.
## Reproduce the evaluation
Real BIRDS workbooks are NOT included; set `CUBESAT_DATA_DIR` to the folder containing TSURU.xlsx, NEPALISAT.xlsx, RAAVANA.xlsx, UGUISU.xlsx (Windows PowerShell: `$env:CUBESAT_DATA_DIR="C:\data"`). From `scripts/`:
```
python build_ledger.py                 # regenerates evaluation/dev_runs/TRAINING_DATA_LEDGER.csv (overwrites)
python dev_compare.py abs rel hyb1 hyb2   # ~5 min; overwrites evaluation/dev_runs/dev_results_*.csv
python dev_select.py                   # applies evaluation/selection_protocol.json -> selection_decision.json
python build_final_release.py          # rebuilds into rebuild_check/ (does not touch the frozen model)
python final_holdout_eval.py           # needs only files in evaluation/sealed_holdout/ (included); re-uses FREEZE.json
python latency.py ; python make_release_figures.py
```
The sealed synthetic holdout (telemetry, truth, generator params, SEAL.json) is included, so `final_holdout_eval.py` and `latency.py` run without the real workbooks.
## Add a new satellite
1. Put telemetry in a supported layout or write a mapping JSON; one session per sheet/CSV.
2. Run `predict`. If it exits 2 with `IncompatibleBaselineError`, the new satellite's V/I scale differs from the reference: a new baseline is required (do not loosen the guard).
3. To build a baseline: load verified-normal sessions (`load_birds_style_workbook` / `load_flat_csv`), `CubeSatKNNPipeline(DetectorConfig(feature_set="abs", k=20, threshold_percentile=99.5)).fit([tables], channel_exclusions=<set of (satellite, session, channel) from a ledger>)`, then `save_artifacts(dir)`. Review sessions with suspect channels BEFORE including them (see ledger rules in `scripts/build_ledger.py`); validate on held-out data before relying on it.
4. A satellite with 1 panel works with `abs`; `rel`/`hyb2` need >=2 panels.
## Layout
`cubesat_knn/` package | `scripts/` | `tests/` | `evaluation/` (all tables, FREEZE/SEAL records, figures, sealed holdout) | `candidates/` | `TRAINING_DATA_LEDGER.csv` | `CHECKSUMS.txt`
## Known limitations (short)
Subtle/partial faults are mostly missed by the selected model; severity estimate is weak; evidence is synthetic or rule-derived (no verified real fault labels); EGS-7 was unavailable; no Raspberry Pi / Windows test.
