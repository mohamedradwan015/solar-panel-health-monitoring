# Test results

Run in the development sandbox on 2026-10-27: Linux x86_64, Python 3.12.3, numpy 2.4.4, pandas 3.0.2, scikit-learn 1.8.0, scipy 1.17.1 (the versions the model release was built with), Flask 3.1.3, headless Chromium via Playwright.

* `python -m unittest discover -s tests -v`: **55 tests, 55 passed** (about 22 s).
* `python tests/ui_smoke.py`: **27 of 27 browser checks passed**.

## Requirement-by-requirement

| # | Test | Result | What was actually checked |
|---|---|---|---|
| 1 | Model loading | Passed | Artifacts load with the SHA-256 manifest check; threshold equals `thresholds.json`; a flipped byte, an edited manifest (pin) and a missing file are each refused with a clear message. |
| 2 | Input validation | Passed | Empty, header-only, binary, fake/corrupt xlsx, `.xls`, `.txt`, unknown columns, missing time or current column, duplicate and backwards timestamps, missing/non-numeric/negative values, too-few rows, wrong-unit data (mA labelled A, mV labelled V, 3x current) refused by the scale guard, row limit, oversize upload (HTTP 413), bad JSON mapping. |
| 3 | Preprocessing | Passed | Canonical table identical to the original `load_flat_csv`; V and I converted exactly once (V to mV, A to mA); 1 to 7 panels detected; feature order `V_V, I_mA`; label columns (`fault_flag` ...) have no effect on scores; ISO-only time; long format (units in names, units requested when ambiguous, duplicate panel/time rejected, symbol panel names `+X`, `-Y`); mapping JSON; multi-sheet workbook with an unsupported sheet isolated; Excel scores equal CSV scores to 1e-9. |
| 4 | Real inference | Passed | On the 60,480-timestamp synthetic holdout the app's scores and flags are **bit-identical** to the original `cubesat_knn.cli predict` output, and that CLI output's SHA-256 (`cdf3fb68ea91ec01...`) **equals the release's frozen predictions hash**. 241,920 panel readings, 241,740 scored, 180 unscorable (missing P3 readings), 9,980 flagged. Flag == (score > threshold) for every row. |
| 5 | Visualisation consistency | Passed | Chart voltages equal the input voltages (4 d.p. rounding); the union of intervals reproduces the model's flagged timestamps exactly for each panel; interval counts sum to the flag counts; with forced decimation every flagged reading and every panel min/max survives. In the browser, red pixels appear on the canvas and the per-panel strip when flags exist, and disappear when panels are hidden. |
| 6 | No-fault case | Passed (synthetic) | A 150-row slice of the **synthetic** holdout (rows 420 to 570) in which no fault was injected and the model flagged nothing: 0 flagged readings, 0 intervals, no bands, all panels "Within expected alert rate". This is a slice of the synthetic benchmark, not real flight data. No real no-fault file was available. |
| 7 | Fault case with ground truth | Passed | Labels used only after inference. Sample level: TP 7,510, FP 2,470, FN 3,273, TN 228,487 (precision 0.753, recall 0.696, F1 0.723, false-alert rate 1.07 %); 12 of 12 injected events have at least one alert; 0 of 8,519 healthy dimmed readings flagged. These equal the numbers in the release's `FINAL_EVALUATION_REPORT.md`. Synthetic data only. |
| 8 | Downloads | Passed | predictions/intervals/flagged CSV row counts and columns; PNG is a valid image of at least 1600 px; PDF is valid; spreadsheet-formula injection neutralised; browser downloads produce a real PNG and CSV. |
| 9 | Regression | Passed | All 13 files of `cubesat_knn/` hash-identical to the release; model files match the release `CHECKSUMS.txt`; app code contains no fitting, thresholding, pickle or random-number logic. |
| 10 | Deployment readiness | Partly verified | `python app.py` starts from another working directory and serves `/`, static files and `/api/health` with the model loaded; pinned requirements and config files present; no secrets or external network calls in the source. **Not verified:** gunicorn, waitress, the Dockerfile, Render or Hugging Face deployment (no network, Docker or those packages in the sandbox). |

Additional behaviour tested: stale results never shown after a new upload; a failed re-analysis leaves the earlier result untouched; a scale-guard failure returns an error and no result; deleting an upload removes its files; expired uploads are cleaned; path-traversal tokens return 404; security headers present; mobile viewport (390 px) has no horizontal scrolling.

## Not tested / unverified
* No real BIRDS workbook was part of the upload, so no real-telemetry run was made. Excel handling was tested with workbooks generated from synthetic data in BIRDS column style.
* Detection quality on real flights is unknown (see KNOWN_LIMITATIONS.md).
* Other browsers (Firefox, Safari, Edge), real touch devices, and screen readers were not tested; keyboard operation of the chart was implemented but only mouse operation was exercised.
* Concurrency under load and very large uploads near the limits (25 MB, 400,000 timestamps) were not stress-tested; the 60,480-timestamp file was the largest run.
* Windows batch files and `waitress` were not run (Linux sandbox).
* Public deployment and URL: **none exists**.
