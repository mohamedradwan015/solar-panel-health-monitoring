# Acceptance checklist (evidence from executed runs)
Verdict: **READY FOR SOFTWARE TESTING.** Scientific validation and deployment acceptance: **INCOMPLETE** (reasons at the end). Performance criteria were predeclared; none were loosened after seeing results.
| # | criterion | result | evidence |
|---|---|---|---|
| 1 | Primary artifact exists and loads via trusted loader | PASS | `cubesat_knn.cli verify` -> `feature_set=abs files=6 mismatches=[]`; `load_trusted_artifacts` used by tests and by the holdout CLI run |
| 2 | Schema matches config and reference arrays | PASS | feature_schema.json = [V_V, I_mA]; model_arrays reference shape (153834, 2); test_pipeline/test_v2 artifact tests |
| 3 | Scaler and threshold applied as documented | PASS | AUDIT_OF_IMPLEMENTATION.md; tests: predict never refits; threshold constant; parity |
| 4 | Predictions repeatable | PASS | bit-exact repeat and save->load parity (test_v2), identical rebuild (determinism_check.json, reproducibility_check.json) |
| 5 | New/updated tests pass | PASS | 58 passed, 0 failed, 0 skipped (47 test functions, parametrised) - `evaluation/test_results_final.txt`, run with `tests/run_tests.py` because pytest is not installed |
| 6 | Inference needs no training data | PASS | CLI predict run from artifact + package only |
| 7 | Manifest verifies | PASS | 6/6 file hashes; manifest sha256 in FREEZE.json equals current |
| 8 | ZIP opens and contains required package | see final response (verified after packaging) | `CHECKSUMS.txt` |
| 9 | Source files and historical artifacts unchanged | PASS | `preservation_hashes_BEFORE.json` vs `preservation_hashes_AFTER.json` |
| 10 | Evaluation reproducible from documented commands | PARTIAL-PASS | abs dev results, protocol decision and final model rebuild reproduced; the other candidates' dev runs, holdout and latency were run once from the release/working copy (holdout script is deterministic given frozen files) |
| 11 | Schema/scale incompatibilities reported | PASS | IncompatibleBaselineError, CLI exit 2 (tests); x3 current refused in dev; synthetic benchmark workbook refused in v0.1 |
| 12 | No held-out evaluation data used to fit/calibrate | PASS (with note) | holdout sealed before any run; model frozen before predictions; final reference = ledger-INCLUDE real data only. NOTE: dev benchmarks reused real sessions that are also in the final reference (the final model is trained on them), so dev numbers are not final-model hold-outs; only the SYN7 holdout is |
Other requirements: no `.joblib`/pickle loaded or present (PASS); DevicePower_TB_179.csv unused (file absent); HANDI excluded (PASS); scale guard/integrity checks/quality safeguards retained (PASS); EGS-7: **NOT RUN - files do not exist**; EGS-7 adapter test: **NOT RUN**; target-hardware latency: **NOT RUN**; Windows execution: **NOT RUN**; Git status: not a Git repository.
## Why scientific/deployment acceptance is incomplete
No verified real-fault labels; selected model misses subtle partial/stuck-cell faults (dev recall 0.00-0.05 for three fault types; real UGUISU +Y pattern flagged 0.2%); severity estimator weak; EGS-7 unavailable so the intended final benchmark was not evaluated; holdout is synthetic and shares fault families with dev injection.
