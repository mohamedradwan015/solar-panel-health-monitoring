"""Tests 2 and 3: input validation, units, panel detection, feature order, formats."""
import unittest, io, zipfile, json
from pathlib import Path
import numpy as np, pandas as pd
from tests.common import ROOT, _tmp, make_flat_csv, syn7_csv
from spm import ingest
from spm.model_service import ModelService
from cubesat_knn.data_loader import load_flat_csv

M = ModelService(); W = Path(_tmp)

def insp(path, ext=None, **kw):
    return ingest.inspect_file(path, ext or path.suffix.lower(), path.name, M, "T", **kw)

def codes(rep, s=0): return [m["code"] for m in rep["sheets"][s]["messages"]]


class Validation(unittest.TestCase):
    def test_empty_and_binary_and_wrong_type(self):
        p = W / "e.csv"; p.write_bytes(b"")
        with self.assertRaises(ingest.IngestError) as c: insp(p)
        self.assertEqual(c.exception.code, "empty_file")
        p = W / "b.csv"; p.write_bytes(b"\x00\x01\x02binary")
        with self.assertRaises(ingest.IngestError) as c: insp(p)
        self.assertEqual(c.exception.code, "not_text")
        p = W / "x.xls"; p.write_bytes(b"abc")
        with self.assertRaises(ingest.IngestError) as c: insp(p, ".xls")
        self.assertEqual(c.exception.status, 415)
        p = W / "bad.xlsx"; p.write_bytes(b"PKnot really a zip")
        with self.assertRaises(ingest.IngestError) as c: insp(p)
        self.assertEqual(c.exception.code, "corrupt_xlsx")
        p = W / "fake.xlsx"; p.write_bytes(b"hello world")
        with self.assertRaises(ingest.IngestError) as c: insp(p)
        self.assertEqual(c.exception.code, "not_xlsx")

    def test_header_only_and_unknown_columns(self):
        p = W / "h.csv"; p.write_text("elapsed_seconds,P1_voltage_V,P1_current_A\n")
        s = insp(p)["sheets"][0]; self.assertEqual((s["status"], s["messages"][0]["code"]), ("error", "empty_file"))
        p = W / "u.csv"; p.write_text("foo,bar,baz\n1,2,3\n4,5,6\n")
        rep = insp(p); s = rep["sheets"][0]
        self.assertEqual(s["status"], "error"); self.assertEqual(rep["usable_sheets"], [])
        self.assertIn("column mapping", s["messages"][0]["text"]); self.assertEqual(s["details"]["columns"], ["foo", "bar", "baz"])

    def test_missing_time_column_and_missing_current(self):
        p = W / "nt.csv"; p.write_text("P1_voltage_V,P1_current_A\n4.5,0.1\n4.6,0.1\n")
        self.assertEqual(insp(p)["sheets"][0]["status"], "error")
        p = W / "ni.csv"; p.write_text("elapsed_seconds,P1_voltage_V\n0,4.5\n10,4.6\n")
        s = insp(p)["sheets"][0]; self.assertEqual(s["status"], "error"); self.assertIn("feature_schema", codes(insp(p)))

    def test_scale_guard_refuses_wrong_units(self):
        # current stored in mA but labelled A: 1000x too large -> refused, not scored
        p = W / "sc.csv"; df = make_flat_csv(W / "sc0.csv"); d = pd.read_csv(df)
        d["P1_current_A"] *= 1000; d["P2_current_A"] *= 1000; d.to_csv(p, index=False)
        rep = insp(p); self.assertEqual(rep["sheets"][0]["status"], "error"); self.assertIn("scale_guard", codes(rep))
        # voltage given in mV but labelled V
        d = pd.read_csv(df); d["P1_voltage_V"] *= 1000; d["P2_voltage_V"] *= 1000; d.to_csv(p, index=False)
        self.assertIn("scale_guard", codes(insp(p)))
        # 3x current is refused (documented release test)
        d = pd.read_csv(df); d["P1_current_A"] *= 3; d["P2_current_A"] *= 3; d.to_csv(p, index=False)
        self.assertIn("scale_guard", codes(insp(p)))

    def test_quality_warnings(self):
        d = pd.read_csv(make_flat_csv(W / "q0.csv"))
        d = pd.concat([d, d.iloc[[10, 11]]]).reset_index(drop=True)           # duplicate timestamps
        d.loc[5, "elapsed_seconds"] = 3.0                                     # goes backwards
        d = d.astype(object); d.loc[20, "P1_voltage_V"] = None; d.loc[21, "P2_current_A"] = "abc"   # missing / non-numeric
        d.loc[30, "P1_voltage_V"] = -1.0                                      # negative
        p = W / "q.csv"; d.to_csv(p, index=False); c = codes(insp(p))
        for want in ("duplicate_timestamps", "non_monotonic", "missing_values", "nonnumeric", "negative"): self.assertIn(want, c)
        self.assertEqual(insp(p)["sheets"][0]["status"], "ok")                 # warnings do not block

    def test_few_rows_warning(self):
        p = make_flat_csv(W / "few.csv", n=10); self.assertIn("few_rows", codes(insp(p)))


class Preprocessing(unittest.TestCase):
    def test_canonical_table_identical_to_original_loader(self):
        p = make_flat_csv(W / "par.csv", panels=("P1", "P2", "P3"))
        ref, _ = load_flat_csv(p, "T", None, "par")
        mine = ingest.load_tables(p, ".csv", "par.csv", [], "T")[0][1]
        pd.testing.assert_frame_equal(mine.reset_index(drop=True), ref.reset_index(drop=True), check_like=True)

    def test_units_converted_once(self):
        p = make_flat_csv(W / "un.csv"); t = ingest.load_tables(p, ".csv", "un.csv", [], "T")[0][1]
        raw = pd.read_csv(p)
        np.testing.assert_allclose(t["V:P1"], raw["P1_voltage_V"] * 1000); np.testing.assert_allclose(t["I:P1"], raw["P1_current_A"] * 1000)
        p2 = make_flat_csv(W / "un2.csv", v_unit="mV", i_unit="mA"); t2 = ingest.load_tables(p2, ".csv", "un2.csv", [], "T")[0][1]
        np.testing.assert_allclose(t2["V:P1"], pd.read_csv(p2)["P1_voltage_mV"]); np.testing.assert_allclose(t2["I:P1"], pd.read_csv(p2)["P1_current_mA"])

    def test_panel_count_and_names_detected(self):
        for names in (("P1",), ("P1", "P2", "P3", "P4", "P5", "P6", "P7")):
            rep = insp(make_flat_csv(W / "pc.csv", panels=names)); self.assertEqual(rep["sheets"][0]["panels"], list(names))

    def test_model_feature_order(self):
        self.assertEqual(M.pipe.feature_names_, ["V_V", "I_mA"]); self.assertEqual(M.pipe.det.feature_names_, ["V_V", "I_mA"])
        t = ingest.load_tables(make_flat_csv(W / "fo.csv"), ".csv", "fo.csv", [], "T")[0][1]
        F = M.features(t); self.assertAlmostEqual(F["V_V"].iloc[0] * 1000, F["V_mV"].iloc[0])

    def test_label_columns_never_reach_the_model(self):
        d = pd.read_csv(make_flat_csv(W / "lab.csv")); d["fault_flag"] = d["P1_voltage_V"] > 4.9; d["fault_type"] = "x"; d["severity_true"] = 0.3
        p = W / "lab2.csv"; d.to_csv(p, index=False); rep = insp(p)
        self.assertEqual(rep["sheets"][0]["status"], "ok"); self.assertIn("unmapped", codes(rep))
        a = M.predict(ingest.load_tables(p, ".csv", "lab2.csv", [], "T")[0][1]); b = M.predict(ingest.load_tables(W / "lab.csv", ".csv", "lab.csv", [], "T")[0][1])
        np.testing.assert_array_equal(a["score"].to_numpy(), b["score"].to_numpy())

    def test_iso_only_time_column(self):
        d = pd.read_csv(make_flat_csv(W / "iso0.csv", with_iso=True)).drop(columns="elapsed_seconds"); p = W / "iso.csv"; d.to_csv(p, index=False)
        t = ingest.load_tables(p, ".csv", "iso.csv", [], "T")[0][1]; self.assertAlmostEqual(float(t["t_s"].iloc[1] - t["t_s"].iloc[0]), 10.0)


class LongFormat(unittest.TestCase):
    def _long(self, vcol, icol, extra=None, n=300):
        w = pd.read_csv(make_flat_csv(W / "lw.csv", n=n)); rows = []
        for pn in ("P1", "P2"):
            rows.append(pd.DataFrame({"elapsed_seconds": w["elapsed_seconds"], "panel_id": pn, vcol: w[f"{pn}_voltage_V"], icol: w[f"{pn}_current_A"]}))
        d = pd.concat(rows).sample(frac=1, random_state=1)
        p = W / "long.csv"; d.to_csv(p, index=False); return p, w

    def test_units_in_names_are_used_and_result_matches_wide(self):
        p, w = self._long("voltage_V", "current_A"); rep = insp(p); s = rep["sheets"][0]
        self.assertEqual((s["status"], s["panels"]), ("ok", ["P1", "P2"])); self.assertTrue(s["long_format"])
        t = ingest.load_tables(p, ".csv", "long.csv", [], "T")[0][1].sort_values("t_s")
        np.testing.assert_allclose(t["V:P2"].to_numpy(), w["P2_voltage_V"].to_numpy() * 1000)

    def test_ambiguous_units_are_requested_not_guessed(self):
        p, _ = self._long("voltage", "current"); s = insp(p)["sheets"][0]
        self.assertEqual(s["status"], "needs_input"); self.assertEqual({x["field"] for x in s["needs_input"]}, {"voltage", "current"})
        with self.assertRaises(ingest.IngestError): ingest.load_tables(p, ".csv", "long.csv", [], "T")
        s = insp(p, units={"voltage": "V", "current": "A"})["sheets"][0]; self.assertEqual(s["status"], "ok")

    def test_wrong_unit_choice_is_caught_by_scale_guard(self):
        p, _ = self._long("voltage", "current"); s = insp(p, units={"voltage": "mV", "current": "A"})["sheets"][0]
        self.assertEqual(s["status"], "error"); self.assertIn("scale_guard", [m["code"] for m in s["messages"]])

    def test_duplicate_panel_time_pairs_rejected(self):
        p, _ = self._long("voltage_V", "current_A"); d = pd.read_csv(p); pd.concat([d, d.iloc[:3]]).to_csv(p, index=False)
        s = insp(p)["sheets"][0]; self.assertEqual(s["status"], "error"); self.assertEqual(s["messages"][0]["code"], "duplicate_observations")

    def test_panel_labels_with_symbols_are_kept(self):
        p, _ = self._long("voltage_V", "current_A"); d = pd.read_csv(p); d["panel_id"] = d["panel_id"].map({"P1": "+X", "P2": "-Y"}); d.to_csv(p, index=False)
        s = insp(p)["sheets"][0]; self.assertEqual(s["panels"], ["+X", "-Y"])
        tabs = ingest.load_tables(p, ".csv", "long.csv", [], "T"); P = M.predict(tabs[0][1]); self.assertEqual(set(P["channel"]), {"X", "Y"})

    def test_mapping_json_for_generic_headers(self):
        d = pd.read_csv(make_flat_csv(W / "m0.csv", panels=("P1",))); d.columns = ["ts", "col_a", "col_b"]; p = W / "map.csv"; d.to_csv(p, index=False)
        self.assertEqual(insp(p)["sheets"][0]["status"], "error")
        m = {"ts": "time", "col_a": {"measure": "voltage", "channel": "PANELA", "unit": "V"}, "col_b": {"measure": "current", "channel": "PANELA", "unit": "A"}}
        s = insp(p, mapping=m)["sheets"][0]; self.assertEqual((s["status"], s["panels"]), ("ok", ["PANELA"]))


class Excel(unittest.TestCase):
    def _wb(self):
        p = W / "wb.xlsx"
        with pd.ExcelWriter(p) as xw:
            for name, seed in (("March 28", 1), ("April 20", 2)):
                w = pd.read_csv(make_flat_csv(W / "x0.csv", n=400, panels=("P1", "P2"), seed=seed)); df = pd.DataFrame({"Time stamp": w["elapsed_seconds"]})
                for pn, ch in (("P1", "py"), ("P2", "px")):
                    df[f"V{ch} (mV)"] = w[f"{pn}_voltage_V"] * 1000; df[f"I{ch} (mA)"] = w[f"{pn}_current_A"] * 1000
                df.to_excel(xw, sheet_name=name, index=False)
            pd.DataFrame({"note": ["not telemetry"]}).to_excel(xw, sheet_name="README", index=False)
            df.to_excel(xw, sheet_name="test bench", index=False)
        return p

    def test_sheets_listed_unsupported_sheet_isolated(self):
        rep = insp(self._wb()); st = {s["name"]: s["status"] for s in rep["sheets"]}
        self.assertEqual(st, {"March 28": "ok", "April 20": "ok", "README": "error", "test bench": "ok"})
        self.assertEqual(rep["usable_sheets"], ["March 28", "April 20", "test bench"])
        self.assertEqual([s["mode"] for s in rep["sheets"]][-1], "lab_test")

    def test_only_selected_sheets_loaded_and_missing_sheet_error(self):
        p = self._wb(); t = ingest.load_tables(p, ".xlsx", "wb.xlsx", ["April 20"], "T"); self.assertEqual([x[0] for x in t], ["April 20"])
        with self.assertRaises(ingest.IngestError): ingest.load_tables(p, ".xlsx", "wb.xlsx", ["README"], "T")
        with self.assertRaises(ingest.IngestError): ingest.load_tables(p, ".xlsx", "wb.xlsx", ["nope"], "T")

    def test_excel_scores_equal_csv_scores(self):
        wb = self._wb(); w = make_flat_csv(W / "x1.csv", n=400, panels=("P1", "P2"), seed=2)
        a = M.predict(ingest.load_tables(wb, ".xlsx", "wb.xlsx", ["April 20"], "T")[0][1]); b = M.predict(ingest.load_tables(w, ".csv", "x1.csv", [], "T")[0][1])
        a = a.assign(channel=a["channel"].map({"py": "P1", "px": "P2"})).sort_values(["channel", "t_s"]); b = b.sort_values(["channel", "t_s"])
        np.testing.assert_allclose(a["score"].to_numpy(), b["score"].to_numpy(), rtol=1e-9, atol=1e-12)

    def test_row_limit(self):
        from spm import settings
        old = settings.MAX_OBSERVATIONS; settings.MAX_OBSERVATIONS = 100
        try:
            with self.assertRaises(ingest.IngestError) as c: ingest.load_tables(make_flat_csv(W / "big.csv", n=500), ".csv", "big.csv", [], "T")
            self.assertEqual(c.exception.status, 413)
        finally: settings.MAX_OBSERVATIONS = old


if __name__ == "__main__": unittest.main()
