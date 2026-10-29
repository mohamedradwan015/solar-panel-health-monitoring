"""Tests 4-7: real inference, visualisation consistency, no-fault case, ground-truth evaluation (synthetic holdout)."""
import unittest, json, hashlib, subprocess, sys
from pathlib import Path
import numpy as np, pandas as pd
from tests.common import ROOT, FIX, _tmp, syn7_csv
from spm import ingest, results, settings
from spm.model_service import ModelService

M = ModelService(); W = Path(_tmp)
SYN = syn7_csv(W)
TABS = ingest.load_tables(SYN, ".csv", SYN.name, [], "SYN7_HOLDOUT")
A = results.analyse(M, TABS, SYN.name, "SYN7_HOLDOUT")
RAW = pd.read_csv(SYN)


class RealInference(unittest.TestCase):
    def test_app_scores_equal_original_cli_and_frozen_hash(self):
        out = W / "cli_pred.csv"
        r = subprocess.run([sys.executable, "-m", "cubesat_knn.cli", "predict", "--artifacts", str(ROOT / "model"), "--input", str(SYN),
                            "--satellite", "SYN7_HOLDOUT", "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        cli = pd.read_csv(out, float_precision="round_trip", low_memory=False); app = A.predictions
        key = ["channel", "source_row"]
        a = app.sort_values(key).reset_index(drop=True); c = cli.sort_values(key).reset_index(drop=True)
        np.testing.assert_array_equal(a["score"].to_numpy(), c["score"].to_numpy())                    # bit-identical scores
        np.testing.assert_array_equal(a["anomaly"].to_numpy(), c["anomaly"].to_numpy())
        self.assertEqual(a["diagnosis"].fillna("").tolist(), c["diagnosis"].fillna("").tolist())
        frozen = json.loads((FIX / "FROZEN_PREDICTIONS.json").read_text())["predictions_sha256"]
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(f"\n   CLI output sha256 {digest[:16]} vs frozen release {frozen[:16]} -> {'identical' if digest == frozen else 'DIFFERENT'}")
        self.assertEqual(digest, frozen, "original CLI output no longer reproduces the release's frozen predictions")

    def test_scores_come_from_knn_distance_not_random(self):
        s1 = M.predict(TABS[0][1].iloc[:500])["score"].to_numpy(); s2 = M.predict(TABS[0][1].iloc[:500])["score"].to_numpy()
        np.testing.assert_array_equal(s1, s2)                                                          # deterministic
        P = A.predictions; self.assertTrue(((P["score"] > P["threshold"]) == P["anomaly"])[P["score"].notna()].all())   # flag == score > threshold, nothing re-thresholded
        self.assertEqual(P["threshold"].nunique(), 1); self.assertAlmostEqual(P["threshold"].iloc[0], 0.025708564284459154, places=15)
        self.assertEqual(int(P["score"].isna().sum()), 180)                                            # exactly the 180 missing P3 readings
        self.assertFalse(P.loc[P["score"].isna(), "anomaly"].any())

    def test_counts_match_release_report(self):
        ov = A.summary["overview"]
        self.assertEqual((ov["panel_readings"], ov["scored_readings"], ov["flagged_readings"]), (241920, 241740, 9980))   # TP+FP = 7510+2470


class VisualisationConsistency(unittest.TestCase):
    def test_series_voltages_are_the_input_voltages(self):
        s = A.series[SYN.stem]; t = np.array(s["t"], float)
        for p in s["panels"]:
            v = np.array([np.nan if x is None else x for x in p["v"]], float)
            want = RAW.set_index("elapsed_seconds")[f"{p['panel']}_voltage_V"].reindex(t).to_numpy()
            np.testing.assert_allclose(v, want, atol=5e-5, equal_nan=True)                             # rounded to 4 d.p. only
        self.assertEqual(s["unit"], "V")

    def test_intervals_reconstruct_model_flags_exactly(self):
        s = A.series[SYN.stem]; t = np.array(s["t"], float); P = A.predictions
        for p in s["panels"]:
            mask = np.zeros(len(t), bool)
            for iv in (i for i in s["intervals"] if i["panel"] == p["panel"]):
                mask |= (t >= iv["t0"]) & (t <= iv["t1"])
            truth = P[(P["channel"] == p["panel"]) & P["anomaly"]]["t_s"]
            self.assertEqual(set(t[mask]), set(truth), p["panel"])
            self.assertEqual(sorted(np.array(t)[p["f"]].tolist()), sorted(truth.tolist()))
            self.assertEqual(int(mask.sum()), len(truth))
        self.assertEqual(sum(1 for _ in s["intervals"]), len(A.intervals))

    def test_interval_bounds_are_consecutive_flagged_readings(self):
        P = A.predictions; g = P[P["channel"] == "P1"].sort_values("t_s"); an = g["anomaly"].to_numpy(); t = g["t_s"].to_numpy()
        iv = A.intervals[A.intervals["panel"] == "P1"]
        for _, r in iv.head(200).iterrows():
            self.assertTrue(an[(t >= r.start_t_s) & (t <= r.end_t_s)].all())
            self.assertEqual(int(((t >= r.start_t_s) & (t <= r.end_t_s)).sum()), r.flagged_readings)
        self.assertEqual(int(iv["flagged_readings"].sum()), int(an.sum()))

    def test_decimation_keeps_every_flag_and_extremes(self):
        old = settings.DISPLAY_MAX_POINTS; settings.DISPLAY_MAX_POINTS = 6000
        try: B = results.analyse(M, TABS, SYN.name, "SYN7_HOLDOUT")
        finally: settings.DISPLAY_MAX_POINTS = old
        s = B.series[SYN.stem]; self.assertTrue(s["decimated"]); self.assertLess(s["n_sent"], s["n_total"])
        for p, p0 in zip(s["panels"], A.series[SYN.stem]["panels"]):
            self.assertEqual(len(p["f"]), len(p0["f"]))                                                # no flagged reading dropped
            v = np.array([np.nan if x is None else x for x in p["v"]], float); v0 = np.array([np.nan if x is None else x for x in p0["v"]], float)
            self.assertEqual(np.nanmin(v), np.nanmin(v0)); self.assertEqual(np.nanmax(v), np.nanmax(v0))
        self.assertEqual(len(B.intervals), len(A.intervals))                                           # statistics use full data


class NoFaultCase(unittest.TestCase):
    """A slice of the SYNTHETIC holdout in which the model happens to flag nothing and no fault was injected."""
    def test_clean_slice_has_no_bands(self):
        truth = pd.read_csv(FIX / "syn7_truth_compact.csv.gz"); bad = set(truth.loc[truth["fault_flag"] | truth["common_mode_dimming"], "elapsed_seconds"])
        P = A.predictions; flagged_t = set(P.loc[P["anomaly"], "t_s"]); t_all = np.sort(RAW["elapsed_seconds"].to_numpy()); start = None
        N = None
        for N in (300, 150, 100, 60):                      # longest flag-free, fault-free window the data contains
            for i in range(0, len(t_all) - N, 10):
                w = t_all[i:i + N]
                if not (set(w) & flagged_t) and not (set(w) & bad): start = i; break
            if start is not None: break
        if start is None: self.skipTest("no flag-free fault-free window exists in the synthetic holdout")
        sl = RAW.iloc[start:start + N]; p = W / "nofault_synthetic_slice.csv"; sl.to_csv(p, index=False)
        tabs = ingest.load_tables(p, ".csv", p.name, [], "SYN"); B = results.analyse(M, tabs, p.name, "SYN")
        self.assertEqual(B.summary["overview"]["flagged_readings"], 0); self.assertEqual(len(B.intervals), 0)
        s = B.series[p.stem]; self.assertEqual(s["intervals"], []); self.assertTrue(all(pn["f"] == [] for pn in s["panels"]))
        self.assertTrue(all(pn["triage"]["level"] == "within_expected" for pn in B.summary["sessions"][0]["panels"]))
        from spm import exports; png = exports.render_chart_png(s, "no flags"); self.assertTrue(png.startswith(b"\x89PNG"))
        print(f"\n   no-fault synthetic slice: rows {start}..{start + N} ({N} rows), 0 flagged readings, 0 bands")


class GroundTruthEvaluation(unittest.TestCase):
    """Labels are used ONLY here, after inference, to evaluate. (Test 7; numbers compared with FINAL_EVALUATION_REPORT.md section 7.)"""
    def test_sample_and_event_level_metrics(self):
        truth = pd.read_csv(FIX / "syn7_truth_compact.csv.gz").rename(columns={"elapsed_seconds": "t_s", "panel_id": "channel"})
        P = A.predictions.merge(truth, on=["t_s", "channel"], how="left"); self.assertEqual(len(P), 241920); self.assertFalse(P["fault_flag"].isna().any())
        sc = P[P["score"].notna()]; y = sc["fault_flag"].astype(bool).to_numpy(); yh = sc["anomaly"].to_numpy(bool)
        tp, fp, fn, tn = int((y & yh).sum()), int((~y & yh).sum()), int((y & ~yh).sum()), int((~y & ~yh).sum())
        prec, rec = tp / (tp + fp), tp / (tp + fn); f1 = 2 * prec * rec / (prec + rec); fpr = fp / (fp + tn)
        print(f"\n   holdout sample-level: TP {tp} FP {fp} FN {fn} TN {tn} precision {prec:.3f} recall {rec:.3f} F1 {f1:.3f} FPR {fpr:.2%}")
        self.assertEqual((tp, fp, fn, tn), (7510, 2470, 3273, 228487))
        self.assertEqual(int(P.loc[P["score"].isna(), "missing_measurement"].all()), 1)                  # unscorable == missing-measurement truth
        ev = P[P["event_id"] >= 0].groupby("event_id")["anomaly"].any(); print(f"   events detected: {int(ev.sum())}/{len(ev)}")
        self.assertEqual((int(ev.sum()), len(ev)), (12, 12))
        dim = sc[sc["common_mode_dimming"].astype(bool) & ~sc["fault_flag"].astype(bool)]    # healthy dimmed rows
        print(f"   healthy dimmed rows: {len(dim)}, flagged {int(dim['anomaly'].sum())}"); self.assertEqual(int(dim["anomaly"].sum()), 0)   # report: 0 % false positives during dimming


if __name__ == "__main__": unittest.main()
