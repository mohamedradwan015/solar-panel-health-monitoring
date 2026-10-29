# Prediction CSV schema (one row per observation x panel)
| column | meaning |
|---|---|
| satellite, session, source_row, channel | identity. `source_row` = spreadsheet row number (header = row 1) or CSV row + 2 |
| t_s | seconds in the session (`t_imputed`=True when extrapolated for untimed rows) |
| mode | `flight` (or `lab_test` for undocumented bench sheets) |
| V_mV, I_mA | panel voltage (mV) / current (mA) after unit normalisation (V->mV, A->mA) |
| all_channels_zero_V | True if every panel voltage is exactly 0 at that instant (readout-glitch pattern) |
| score | mean distance to the k=20 nearest reference points in the scaled (V, I) space. NaN = unscorable. NOT a probability and NOT a power-loss fraction |
| threshold | frozen threshold (same for all rows) |
| anomaly | `score > threshold` (False when unscorable) |
| data_quality_status | `ok` / `warn` (imputed time, imputed feature, all-zero voltage) / `unscorable` (missing voltage or all features missing) |
| quality_warnings | semicolon-separated reasons |
| diagnosis | normal_like, statistical_anomaly, suspected_low_output, suspected_high_output, persistent_channel_deviation, data_quality_issue, unknown_or_insufficient_evidence. Cautious labels, never a confirmed fault |
| direction, top_deviations | which features deviate most from the nearest normal neighbours (scaled units) |
| power_loss_est | estimated fraction of panel power lost, 0-1, ONLY when `severity_status == "estimated"`; else NaN |
| severity_status | not_flagged / estimated / within_healthy_spread (flagged, but the estimate is inside normal panel-to-panel spread) / unscorable_low_illumination_or_missing / unscorable_single_channel / unscorable_no_baseline |
Severity is a separate heuristic (rolling-q90 current ratio vs best sibling), not derived from `score`; see FINAL_MODEL_CARD.md for its accuracy.
