# Solar Panel Health Monitoring

Satellite telemetry analysis and anomaly detection for solar panels. Upload a CSV or Excel file, run the **supplied kNN model**
(`final_release_v1`), and inspect a voltage chart with the readings the model flagged shown as red bands.

* Real inference only: the app loads the release's own model package (`cubesat_knn/`, byte-identical to the release) and its
  integrity-checked artifacts (`model/`). There are no mock predictions, random scores or fallback results.
* If inference cannot run (wrong scale, unreadable columns, missing model files) the app shows an error and produces no results.
* Red bands mean "the model flagged this reading as statistically unusual". They are **not** confirmed physical damage.

## Run it on Windows

1. Install Python 3.11 or newer from python.org (tick *Add Python to PATH*). Tested with Python 3.12.
2. Double-click `setup_windows.bat` once (creates `.venv` and installs the pinned requirements).
3. Double-click `run_windows.bat`. The browser opens at <http://127.0.0.1:8000>.

Or by hand in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

macOS / Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py`

## Using the app

1. Drop a file on the upload area (or click *Use the bundled synthetic sample*).
2. Read the file check: panels found, units converted, data-quality warnings, scale check. Workbooks list each sheet; choose the sessions to analyse.
3. Press **Run analysis**.
4. Review the overview, panel cards, the **Panel Voltages** chart (drag to zoom, double-click to reset, click legend chips to hide panels), the tables, and download CSV / PNG / PDF.

### Accepted files

| Layout | Example columns |
|---|---|
| BIRDS workbook (one sheet = one session) | `Time stamp`, `Vpy (mV)`, `Ipy (mA)`, ... |
| Flat file, one column pair per panel | `elapsed_seconds` or `timestamp_utc`, `P1_voltage_V`, `P1_current_A`, `P2_voltage_V`, ... |
| Long file, one reading per row | `panel_id`, `elapsed_seconds`, `voltage_V`, `current_mA` |
| Anything else | supply a column mapping (advanced) |

`.csv`, `.xlsx`, `.xlsm`. Not `.xls` (save as `.xlsx`). Units must be in the column names or chosen explicitly; they are never guessed. Any number of panels is accepted.

## What the model needs (short version)

The detector compares each (voltage, current) pair with 153,834 normal readings from four BIRDS satellites. A reading is flagged when its mean distance to the
20 nearest reference readings exceeds a fixed threshold (the 99.5th percentile of out-of-session reference scores, 0.025709). That means about 0.5 % of healthy readings
are flagged by design. **The score is a distance, not a probability.** The app refuses data whose voltage or current scale is more than 2x away from the reference.
Details: `docs/MODEL_AND_DATA_REQUIREMENTS.md`, `docs/KNOWN_LIMITATIONS.md`, and the release's own `docs/release_docs/FINAL_MODEL_CARD.md`.

## Tests

```
python -m unittest discover -s tests -v      # 55 automated tests, about 25 s
pip install -r requirements-dev.txt && python -m playwright install chromium
python tests/ui_smoke.py                      # optional real-browser test
```

Results of the run made when this project was built are in `docs/TEST_RESULTS.md`, including what was not tested.

## Deploying for a public link

See `docs/DEPLOYMENT.md` (Render, Hugging Face Spaces, Docker). No public URL exists until you deploy; none was created while building this project.

## Project layout

```
app.py, wsgi.py        entry points (local / production)
spm/                   application code: ingest, model_service, results, exports, storage, server
cubesat_knn/           the supplied model package, unmodified
model/                 supplied model artifacts + MANIFEST.sha256 pin
static/                interface (HTML, CSS, canvas chart)
sample_data/           bundled synthetic sample
tests/                 automated tests and fixtures
docs/                  requirements, limitations, deployment, test results, original release documents
```

## Privacy

Uploads are stored in a temporary folder under an unguessable name, never shared between visitors, deleted after 2 hours or when you press *Delete my uploaded data*.
Model files are loaded only from `model/` (SHA-256 verified); uploaded files are parsed as data only and never executed or unpickled.
