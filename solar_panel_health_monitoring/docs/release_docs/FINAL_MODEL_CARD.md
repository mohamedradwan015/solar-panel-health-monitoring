# Model card: CubeSat solar-panel KNN anomaly detector, release v1 (package 0.2.0)
**Primary model:** feature set **`abs`** (inputs: panel voltage V_V, panel current I_mA), KNN k=20, threshold = 99.5th percentile of out-of-session reference scores (0.0257 in scaled units).
**Status: SELECTED under the predeclared protocol, but SCIENTIFICALLY UNVALIDATED for real faults - deliver as a software-testing candidate.**
**Artifact:** repository root (`config.json, feature_schema.json, model_arrays.npz, thresholds.json, provenance.json, severity_baseline.json, manifest.json`). Optional comparison artifacts: `candidates/{rel,hyb1,hyb2}` (same policy, their own dev-selected k/percentile) - do not load these for deployment.

## Intended use
Per-panel screening of CubeSat panel telemetry (V, I) for statistical anomalies on BIRDS-bus-like satellites, any number of panels (1+). It flags observations that are unusual relative to normal operation; it does not confirm faults, localise cells, or predict failures.

## Reference data (unified; ledger `TRAINING_DATA_LEDGER.csv`)
153,834 trusted-normal panel observations from TSURU, NEPALISAT, RAAVANA and UGUISU flight sessions after exclusions. Excluded: all RAAVANA +Y and UGUISU +Y sessions (published-rule anomalies in >=51% of rows; suspect, not confirmed failures); TSURU lab-test sheets; 4 channel-sessions marked UNCERTAIN (low current with sunlit siblings: NEPALISAT pz x2, TSURU mz July 17, TSURU pz Dec 13); HANDI (self-described simulated copy of TSURU); the synthetic benchmark workbook. Row-level: rule-anomalous rows, all-zero readouts, missing values. Effect of including the UNCERTAIN channels was negligible on dev metrics (TSURU-fold natural alert rate 0.32% -> 0.38%; NEPALISAT identical).

## Threshold policy (fixed before final evaluation)
99.5th percentile of leave-one-session-out scores over the trusted reference. Chosen on the dev-validation injection as the setting with highest recall at FPR <= 2% (dimming events included); it is not a universal threshold: natural alert rates on held-out data were 0.3-0.6% (healthy satellites).

## Performance (see FINAL_EVALUATION_REPORT.md)
* Dev natural alert rate, unseen healthy data: 0.60% (later TSURU sessions), 0.32% (TSURU held out), 0.60% (NEPALISAT held out), 0.40% (UGUISU held out).
* Dev injected faults (confirm/LOSO): sample recall 0.37-0.89, FPR 0.4-0.6%, event recall 7/10, 7/10, 4/4, false events 3-8 per benchmark.
* Sealed synthetic 7-day holdout (not EGS-7): precision 0.75, recall 0.70, F1 0.72, FPR 1.07%, 12/12 events detected, 0 false events, 0.0% false positives during common-mode dimming.
* Real RAAVANA +Y (total loss of output, ~0 V): flagged 100% (LOSO).

## Known weaknesses
* Subtle faults are largely missed in dev tests: partial degradation recall 0.05, intermittent bursts 0.00, stuck-cell-like 0.00 (confirm benchmark). On the real UGUISU +Y pattern (V 2.3-3 V, I ~5 mA; rule-derived label) the `abs` model flags only 0.2% of samples; `rel` flags 39.9% but fails false-positive criteria. A deployment aimed at partial/stuck-cell faults is NOT supported by this model.
* Dimming robustness depends on the conservative threshold (at k=5/99% the dev dimming FPR was 36%).
* Severity (`power_loss_est`) covers only ~22% of truly faulty flagged holdout rows (others: below healthy spread or low illumination); where issued MAE = 0.049, corr 0.42 (synthetic truth). It is experimental; do not use for decisions.
* Scale guard checks only the 99th percentile; requires BIRDS-like units/scale; other satellites need a new baseline.
* No target-hardware (Raspberry Pi / flight computer) timing; no real fault ground truth; one real-fault satellite pair only.

## Not claimed
Universal accuracy, physical fault confirmation, cell-level localisation, real-mission detection performance.
