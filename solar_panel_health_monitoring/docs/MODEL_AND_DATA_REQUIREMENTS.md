# Model and data requirements (read from the supplied source code)

## Model
* Package `cubesat_knn` 0.2.0 (`final_release_v1`), unchanged. Production configuration from `model/config.json`: feature set `abs`, level `channel`, k = 20, robust scaler, threshold percentile 99.5.
* Algorithm: each panel reading (one row per timestamp and panel) is described by two features, voltage `V_V` (volts) and current `I_mA` (milliamps), in that order. Features are scaled with the stored robust scaler. The score is the mean distance to the 20 nearest of 153,834 normal reference readings (`model/model_arrays.npz`).
* Flag: `score > threshold`, threshold = 0.025708564284459154 (stored, never recomputed). This is a percentile of out-of-session reference scores; it is not a probability. About 0.5 % of healthy readings are flagged by design.
* Entry points used: `CubeSatKNNPipeline.load_trusted_artifacts` (SHA-256 check against `manifest.json`, and the manifest itself is pinned by `model/MANIFEST.sha256`), `predict`, `check_compatibility`; `cubesat_knn.schema_detector.detect_schema`, `cubesat_knn.data_loader.to_canonical`, `cubesat_knn.features.build_channel_features`.
* Per-reading output columns (kept verbatim in the CSV): `score, threshold, anomaly, diagnosis, direction, top_deviations, data_quality_status, quality_warnings, power_loss_est, severity_status, ...`. `power_loss_est` is experimental in the release and not shown in the interface.
* Reference data is embedded in the artifact (leave-one-satellite-out was used to evaluate; the shipped production model uses all four satellites). The app never fits or re-tunes anything on uploaded data, and never feeds label columns to the model (tested).

## Input
| Item | Requirement |
|---|---|
| Time | `elapsed_seconds` / `Time stamp` (seconds) or an ISO timestamp column. Not used by the `abs` score; used for ordering and display. |
| Voltage | one column per panel; unit in the name (`_V`, `_mV`, `(mV)`). Converted once to mV internally, shown in volts. |
| Current | one column per panel; `_A`, `_mA`, `(mA)`. Required for every panel. |
| Panels | any number >= 1. Names from the headers (`P1`, `py`, `+X` ...). |
| Scale guard | the 99th percentile of voltage and of current must be within 0.5x to 2x of the reference (V 5.49 V, I 382 mA). Otherwise the file is refused. |
| Not used | temperature, battery columns, labels. Reported as "not used". |

## Limits (configurable by environment variable)
25 MB upload, 400,000 timestamps per analysis, 80 sheets per workbook, 2 h retention.

## Performance measured here
About 4 s for the 7-day, 60,480-timestamp, 4-panel synthetic file (241,920 panel readings) including parsing; the release documents about 340 MB peak memory.
