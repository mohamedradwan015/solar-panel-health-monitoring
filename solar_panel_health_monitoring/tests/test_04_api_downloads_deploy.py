"""Tests 8 and 10 plus API behaviour: downloads, errors, stale-result protection, isolation, start-up."""
import unittest, io, json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path
import numpy as np, pandas as pd
from PIL import Image
from tests.common import ROOT, _tmp, make_flat_csv, syn7_csv
from spm import settings
from spm.server import create_app

W = Path(_tmp)
APP = create_app(); C = APP.test_client()


def upload(path, name=None, **form):
    return C.post("/api/inspect", data={"file": (io.BytesIO(Path(path).read_bytes()), name or Path(path).name), **form}, content_type="multipart/form-data")


class Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = make_flat_csv(W / "api_a.csv", n=800, panels=("P1", "P2", "P3"))
        r = upload(cls.src); cls.up = r.json; cls.id = cls.up["upload_id"]
        r = C.post("/api/analyze", json={"upload_id": cls.id, "sheets": cls.up["inspect"]["usable_sheets"]}); assert r.status_code == 200, r.json
        cls.res = r.json["summary"]; cls.session = cls.res["sessions"][0]["name"]

    def test_inspect_reports_validation(self):
        self.assertEqual(self.up["file"]["type"], "csv"); s = self.up["inspect"]["sheets"][0]
        self.assertEqual((s["status"], s["panels"], s["rows"]), ("ok", ["P1", "P2", "P3"], 800)); self.assertEqual({c["feature"] for c in s["scale"]}, {"V_V", "I_mA"})
        self.assertTrue(any("-> mV" in u for u in s["unit_conversions"]))

    def test_csv_downloads(self):
        pr = pd.read_csv(io.BytesIO(C.get(f"/api/runs/{self.id}/download/predictions.csv").data))
        self.assertEqual(len(pr), 800 * 3)
        for col in ("session", "panel", "t_s", "V_mV", "I_mA", "score", "threshold", "anomaly", "diagnosis", "data_quality_status"): self.assertIn(col, pr.columns)
        self.assertNotIn("severity_true", pr.columns); self.assertEqual(int(pr["anomaly"].sum()), self.res["overview"]["flagged_readings"])
        iv = pd.read_csv(io.BytesIO(C.get(f"/api/runs/{self.id}/download/intervals.csv").data)); self.assertEqual(len(iv), self.res["overview"]["intervals"])
        fl = pd.read_csv(io.BytesIO(C.get(f"/api/runs/{self.id}/download/flagged.csv").data)); self.assertEqual(len(fl), self.res["overview"]["flagged_readings"])

    def test_png_and_pdf_downloads(self):
        r = C.get(f"/api/runs/{self.id}/download/chart.png?session={self.session}&min_run=3"); self.assertEqual(r.status_code, 200)
        im = Image.open(io.BytesIO(r.data)); self.assertEqual(im.format, "PNG"); self.assertGreaterEqual(im.size[0], 1600); self.assertGreater(len(set(im.convert("RGB").resize((200, 120)).getdata())), 20)
        r = C.get(f"/api/runs/{self.id}/download/report.pdf?session={self.session}"); self.assertTrue(r.data.startswith(b"%PDF")); self.assertGreater(len(r.data), 20000)
        self.assertEqual(C.get(f"/api/runs/{self.id}/download/nonsense.exe").status_code, 404)

    def test_paged_tables(self):
        r = C.get(f"/api/runs/{self.id}/flagged?limit=5&session={self.session}").json; self.assertEqual(r["total"], self.res["overview"]["flagged_readings"]); self.assertLessEqual(len(r["rows"]), 5)
        self.assertEqual(C.get(f"/api/runs/{self.id}/series?session=nope").status_code, 404)

    def test_new_upload_never_shows_old_results(self):
        b = make_flat_csv(W / "api_b.csv", n=300, panels=("P1",), seed=5); up2 = upload(b).json
        self.assertNotEqual(up2["upload_id"], self.id); self.assertEqual(C.get(f"/api/runs/{up2['upload_id']}/summary").status_code, 404)    # no result yet
        r2 = C.post("/api/analyze", json={"upload_id": up2["upload_id"], "sheets": up2["inspect"]["usable_sheets"]}).json["summary"]
        self.assertEqual(r2["overview"]["observations"], 300); self.assertEqual(C.get(f"/api/runs/{self.id}/summary").json["overview"]["observations"], 800)

    def test_reanalysis_replaces_result_and_failed_run_leaves_none(self):
        up = upload(make_flat_csv(W / "api_c.csv", n=400, panels=("P1", "P2"), seed=9)).json; i = up["upload_id"]
        ok = C.post("/api/analyze", json={"upload_id": i, "sheets": up["inspect"]["usable_sheets"]}); self.assertEqual(ok.status_code, 200)
        before = C.get(f"/api/runs/{i}/summary").json
        bad = C.post("/api/analyze", json={"upload_id": i, "sheets": up["inspect"]["usable_sheets"], "mapping": {"P1_voltage_V": "ignore", "P2_voltage_V": "ignore"}})
        self.assertGreaterEqual(bad.status_code, 400); self.assertIn("error", bad.json)
        self.assertEqual(C.get(f"/api/runs/{i}/summary").json, before)       # the failed run did not corrupt or replace the earlier result

    def test_scale_guard_failure_is_an_error_not_a_result(self):
        p = make_flat_csv(W / "api_d.csv", scale_i=1000.0); up = upload(p).json; s = up["inspect"]["sheets"][0]
        self.assertEqual(s["status"], "error"); self.assertEqual(up["inspect"]["usable_sheets"], [])
        r = C.post("/api/analyze", json={"upload_id": up["upload_id"], "sheets": [s["name"]]})
        self.assertEqual(r.status_code, 422); self.assertEqual(r.json["error"]["code"], "scale_guard"); self.assertEqual(C.get(f"/api/runs/{up['upload_id']}/summary").status_code, 404)

    def test_errors(self):
        self.assertEqual(C.post("/api/inspect", data={}, content_type="multipart/form-data").status_code, 400)
        r = upload(W / "api_a.csv", "notes.txt"); self.assertEqual((r.status_code, r.json["error"]["code"]), (415, "unsupported_type"))
        r = upload(W / "api_a.csv", "old.xls"); self.assertEqual(r.status_code, 415)
        self.assertEqual(C.post("/api/analyze", json={"upload_id": self.id, "sheets": []}).status_code, 400)
        for bad in ("../../etc/passwd", "x", "A" * 20):
            self.assertEqual(C.get(f"/api/runs/{bad}/summary").status_code, 404)
        r = C.post("/api/inspect", data={"file": (io.BytesIO(b"elapsed_seconds,P1_voltage_V\n0,1"), "z.csv"), "mapping": "{bad"}, content_type="multipart/form-data"); self.assertEqual(r.json["error"]["code"], "bad_json")

    def test_oversize_upload_rejected(self):
        old = settings.MAX_UPLOAD_MB; settings.MAX_UPLOAD_MB = 1
        try:
            app = create_app(); c = app.test_client(); big = io.BytesIO(b"a,b\n" + b"1,2\n" * 600_000)
            r = c.post("/api/inspect", data={"file": (big, "big.csv")}, content_type="multipart/form-data"); self.assertEqual((r.status_code, r.json["error"]["code"]), (413, "file_too_large"))
        finally: settings.MAX_UPLOAD_MB = old

    def test_delete_removes_data(self):
        up = upload(make_flat_csv(W / "api_e.csv", n=100)).json; i = up["upload_id"]
        self.assertTrue((settings.DATA_DIR / i / "upload.csv").exists()); self.assertEqual(C.delete(f"/api/runs/{i}").status_code, 200)
        self.assertFalse((settings.DATA_DIR / i).exists()); self.assertEqual(C.post("/api/analyze", json={"upload_id": i, "sheets": ["x"]}).status_code, 404)

    def test_expired_runs_are_cleaned(self):
        from spm import storage
        up = upload(make_flat_csv(W / "api_f.csv", n=100)).json; d = settings.DATA_DIR / up["upload_id"]; old = time.time() - 10 * 3600; os.utime(d, (old, old))
        self.assertGreaterEqual(storage.cleanup_expired(force=True), 1); self.assertFalse(d.exists())

    def test_formula_injection_neutralised_in_csv_downloads(self):
        w = pd.read_csv(make_flat_csv(W / "inj0.csv", n=200, panels=("P1",))); d = pd.DataFrame({"elapsed_seconds": w["elapsed_seconds"], "panel_id": "=HYPERLINK(1)", "voltage_V": w["P1_voltage_V"], "current_A": w["P1_current_A"]})
        p = W / "inj.csv"; d.to_csv(p, index=False); up = upload(p).json; s = up["inspect"]["sheets"][0]; self.assertEqual(s["status"], "ok")
        C.post("/api/analyze", json={"upload_id": up["upload_id"], "sheets": [s["name"]]}); pr = pd.read_csv(io.BytesIO(C.get(f"/api/runs/{up['upload_id']}/download/predictions.csv").data))
        self.assertTrue(pr["panel"].astype(str).str.startswith("'=").all())

    def test_security_headers_and_no_store(self):
        r = C.get("/api/health"); self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff"); self.assertIn("no-store", r.headers["Cache-Control"]); self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
        self.assertTrue(C.get("/").data.startswith(b"<!doctype html>"))

    def test_sample_endpoint_runs_end_to_end(self):
        d = C.post("/api/sample").json; self.assertTrue(d["file"]["is_sample"]); r = C.post("/api/analyze", json={"upload_id": d["upload_id"], "sheets": d["inspect"]["usable_sheets"]})
        self.assertEqual(r.json["summary"]["overview"]["panels"], 4); self.assertTrue(r.json["summary"]["is_sample"])


class DeploymentReadiness(unittest.TestCase):
    def test_app_starts_from_another_directory_and_serves(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        env = dict(os.environ, PORT=str(port), HOST="127.0.0.1", SPM_DATA_DIR=str(W / "deploy_runs"), SPM_LOG_DIR=str(W / "deploy_logs"))
        pr = subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=tempfile.gettempdir() if (tempfile := __import__("tempfile")) else None, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                try: h = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)); break
                except Exception: time.sleep(0.5)
            else: self.fail("server did not start")
            self.assertTrue(h["model_loaded"])
            for path in ("/", "/static/css/app.css", "/static/js/app.js", "/static/js/chart.js"): self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).status, 200)
        finally: pr.terminate(); pr.wait(10)

    def test_deployment_files_present_and_requirements_pinned(self):
        for f in ("requirements.txt", "Dockerfile", "Procfile", "render.yaml", ".gitignore", "README.md", "README_AR.md", "wsgi.py", "app.py", "model/manifest.json", "model/model_arrays.npz"):
            self.assertTrue((ROOT / f).exists(), f)
        core = [l.split("==")[0].lower() for l in (ROOT / "requirements.txt").read_text().splitlines() if "==" in l and not l.startswith("#")]
        for pkg in ("numpy", "pandas", "scikit-learn", "scipy", "openpyxl", "flask", "matplotlib", "reportlab"): self.assertIn(pkg, core)
        gi = (ROOT / ".gitignore").read_text(); self.assertIn(".venv", gi); self.assertNotIn("model_arrays", gi)

    def test_no_secrets_or_network_calls_in_source(self):
        src = "".join(p.read_text() for p in list((ROOT / "spm").glob("*.py")) + list((ROOT / "static").rglob("*.js")) + [ROOT / "static/index.html"])
        for bad in ("http://", "https://cdn", "API_KEY", "SECRET", "eval(", "exec(", "subprocess", "os.system"): self.assertNotIn(bad, src.replace("http://www.w3.org", ""), bad)


if __name__ == "__main__": unittest.main()
