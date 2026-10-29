# Final evaluation report (release v1)
All numbers below were produced by the scripts in `scripts/` (commands in README.md); raw tables are in `evaluation/`. Synthetic benchmarks do not measure real spacecraft fault-detection accuracy.

## 1. Environment and preservation
EGS-7 files: **not found** (searched the whole filesystem for `*EGS*`); `DevicePower_TB_179.csv`: not present and never used; HANDI: excluded (self-described simulated TSURU copy); project is not a Git repository; no `.joblib`/pickle file exists and none was loaded; pytest not installed (fallback runner used, environment unchanged). Hashes of 131 original files (uploads + v0.1 project) recorded before work in `evaluation/preservation_hashes_BEFORE.json` and rechecked after (see ACCEPTANCE_CHECKLIST). The 5 historical manifests verified (25/25 hashes) before use.

## 2. Predeclared protocol (`evaluation/selection_protocol.json`, written and hashed before any candidate was run)
Candidates: abs (V, I), rel (v0.1 relative set), hyb1 (abs + sun_V), hyb2 (abs + sun_V + lit_ratio + i_lit_ratio). Same ledger-applied reference, same sessions, same injected faults (fresh seeds 5101/5102/5201/5202, including healthy common-mode dimming events). k and percentile chosen only on the validation injection (seed 5101). Eligibility E1-E6 and ranking in the protocol file; real-data agreement with published rules was **not** a criterion (circular).

## 3. Candidate comparison (`evaluation/feature_comparison.csv`)
| candidate | E1 FPR<=2.5% | E2 natural<=2.5% | E3 dimming FPR<=3% | E4 3/2-channel<=3% | E5 scale guard | E6 total loss | eligible | rank score | k / pct |
|---|---|---|---|---|---|---|---|---|---|
| **abs** | pass | pass | pass | pass | pass | pass | **yes** | 0.675 | 20 / 99.5 |
| rel | fail (TSURU 3.7%) | pass | fail (6.7%, 5.6%) | fail (2 ch: 10.3%) | pass | pass | no | 0.614 | 5 / 99 |
| hyb1 | pass | pass | fail (4.4% on one benchmark) | pass | pass | pass | no | 0.740 | 20 / 99 |
| hyb2 | pass | pass | fail (3.2%) | fail (3 ch 4.9%, 2 ch 12.2%) | pass | pass | no | 0.458 | 5 / 99 |
`abs` was the only eligible candidate and is selected by the protocol. `hyb1` had higher recall (0.63 vs 0.55 mean sample recall) but failed the dimming criterion; it is the natural next candidate if the dimming limit were relaxed (not done, to avoid post-hoc loosening). `rel` is the only candidate that responds to the real UGUISU +Y pattern (39.9% flagged vs 0.2% for abs) but produced 36 and 44 false events on the two TSURU injected benchmarks (7 on NEPALISAT). Recall by fault type (confirm benchmark, abs/rel/hyb1/hyb2): zero output 1.00/1.00/1.00/0.93; sudden shift 0.48/0.04/0.61/0.52; noise 0.51/0.38/0.62/0.70; stuck-cell 0.00/0.54/0.11/0.02; partial degradation 0.05/0.02/0.25/0.02; bursts 0.00/0.03/0.00/0.00.

## 4. Cross-satellite (leave-one-satellite-out) - `evaluation/cross_satellite_results.csv`
Natural alert rate (fraction of scored panel-observations flagged), abs: TSURU 0.32%, NEPALISAT 0.60%, UGUISU 0.40%, RAAVANA 20.4% (its +Y panel is 100% flagged, the other four panels 0.2-0.9%). Injected: TSURU held out (last 8 sessions, 10 events): recall 0.37, FPR 0.41%, 7/10 events, 3 false events; NEPALISAT held out (4 events): recall 0.89, FPR 0.62%, 4/4, 8 false events. UGUISU +Y: abs flags 0.2% (rule-positive 53.5%, circular label) - missed. Limits: four satellites of one bus design; two of them contain the suspect channels; folds do not show generalisation to other missions.

## 5. Chronological TSURU - `evaluation/chronological_results.csv`
Reference = first 24 date-ordered flight sessions; validation sessions 25-32; confirm sessions 33-40. abs natural alert rate: 0.53% (validation), 0.60% (confirm). Confirm injected: recall 0.39, FPR 0.56%, precision 0.81, 7/10 events, 4 false events, dimming FPR 1.0%. Session dates: workbook year assumed 2021 for sheets without a year.

## 6. Robustness (abs, chrono model, `synthetic_development_results.csv`)
3 channels kept 0.37% alert, 2 kept 0.38%, 1 kept 0.44%; one channel missing for half the session: 0.34% alert on the others, 10% of rows unscorable (not zero-filled); current x3: refused by the scale guard.

## 7. Sealed final holdout (SYN7; **not EGS-7**) - `evaluation/final_holdout_syn7_results.csv`
Design: 7 days, 10 s cadence, 4 panels, 12 scheduled fault events (6 types x 2), 3 common-mode dimming windows (healthy), one 30-minute missing-data window on P3, seed 9001, generated and hashed (`sealed_holdout/SEAL.json`) before any candidate ran; orbit/eclipse/tumbling physics with V and I marginals quantile-matched to real TSURU+NEPALISAT flight data; fault types are the same families as in dev injection. Model frozen first (`FREEZE.json`), predictions produced through the CLI and hashed (`FROZEN_PREDICTIONS.json`), truth opened afterwards. Not tuned on: all choices predate it.
* Compatibility: scale guard passed (I p99 ratio 0.98, V p99 ratio 1.00); cadence exactly 10 s, no duplicates, no truth/counterfactual columns in telemetry; unscorable rows = exactly the 180 missing P3 rows (matches truth).
* Sample-level (241,740 scorable): TP 7,510, FP 2,470, FN 3,273, TN 228,487; precision 0.753, recall 0.696, F1 0.723, FPR 1.07%, PR-AUC 0.779, ROC-AUC 0.991. Per panel F1: P1 0.775, P2 0.646, P3 0.518 (precision 0.39), P4 0.785.
* Event-level (rule: >=1 alert inside the event; false event = >=3 consecutive alerts away from any event +-60 s): 12/12 detected, 0 missed, 0 false events, median delay 0 s.
* Recall by type: zero output 1.00, noise 0.76, sudden shift 0.76, bursts 0.64, stuck-cell 0.51, partial degradation 0.50.
* Common-mode dimming: 0.0% false positives on 8,519 healthy dimmed rows (vs 1.11% outside dimming).
* Severity: estimate issued for 22% of flagged faulty rows (2,411/7,510); MAE 0.049, median abs error 0.035, bias -0.008, correlation 0.42; none issued on healthy rows; 4,241 flagged faulty rows were "within healthy spread", 858 unscorable.
* Caveats: the holdout is more favourable than dev (sample recall 0.70 vs 0.37-0.39 on real-session injections) because its faults are longer and the generator is quantile-matched to real marginals; it shares fault families with dev injection; the holdout is one realisation (n=12 events). EGS-7 evaluation: **NOT RUN** (`egs7_final_results.csv`).

## 8. Performance (`evaluation/latency_results.json`; 1 vCPU Xeon 2.1 GHz sandbox)
7-day, 4-panel file (241,920 panel-observations): median 2.95 s, mean 2.87 s (5 runs) = ~82k obs/s; 1 panel 62.8k obs/s, 2 panels 90k, 4 panels 74k; KNN scoring of 20,000 rows: 0.13 s (10k reference), 0.23 s (50k), 0.28 s (153.8k); peak RSS 340 MB; model_arrays.npz 2.46 MB. Raspberry Pi / flight hardware: NOT RUN.

## 9. Reproducibility
The abs dev results re-run from the release copy matched the original exactly; re-applying the protocol reproduced the same decision; rebuilding the final model from scratch gave byte-identical config, feature schema, model arrays, thresholds and severity baseline (`evaluation/reproducibility_check.json`, `determinism_check.json`).

## 10. Issues blocking production / scientific acceptance
1. No independent real fault labels; real UGUISU-type partial/stuck-cell faults are missed by the selected model. 2. EGS-7 unavailable. 3. Severity estimator weak. 4. Single-bus data; scale guard coarse. 5. No target-hardware benchmark. 6. Windows untested.
