# Known limitations

**Model (from the release's own model card and evaluation report)**
1. Detection evidence is synthetic or rule-derived. No independently verified real fault labels were available. On the sealed synthetic holdout: precision 0.753, recall 0.696, F1 0.723, false-alert rate 1.07 %, 12 of 12 injected events touched. Real-data behaviour is not demonstrated.
2. Subtle faults (partial degradation, stuck cells, intermittent bursts, slow drift) are mostly missed. A quiet panel does not prove a healthy panel.
3. The reference is four BIRDS satellites with similar voltage/current scales. Generalisation to other satellites is not demonstrated; the scale guard only blocks gross scale mismatches (>2x), not subtler differences such as different cell technology.
4. A flagged share of about 0.5 % on healthy data is expected by construction; the interface says so.
5. Diagnosis wording and `power_loss_est` are cautious, heuristic add-ons of the release and not verified fault classes.

**Application**
6. The coloured panel status ("Within expected alert rate", "Review", "High alert rate", "Insufficient data") is an application convenience (thresholds 2 % and 10 %, or any persistent run for Review). It is not produced by the model and has not been validated. Thresholds are in `spm/settings.py`.
7. Flagged intervals are runs of consecutive flagged readings on one panel in time order. Missing readings break a run. Single-reading flags are common (they include the ~0.5 % false alerts); the chart offers a display-only filter (runs of 3+ / 10+). The CSV always has every flag.
8. Readings without a usable timestamp are scored but cannot be drawn.
9. Long-format and mapping support covers the layouts listed in the README; other layouts need a mapping. Time zone is assumed UTC for ISO timestamps without an offset.
10. `.xls` is not supported (no safe parser installed). Decimal-comma CSVs are not converted.
11. One analysis runs at a time per server process (scoring is serialised); large files take seconds to a minute. No request timeout beyond the web server's.
12. Uploaded data lives on the server's temporary disk for up to 2 h. Free hosting tiers have ephemeral disks, little memory and sleep when idle.
13. The interface is English only. The Arabic README covers setup and use.
