"""Test 1 (model loading) and Test 9 (regression: the supplied model code and artifacts are untouched)."""
import unittest, json, hashlib, shutil, tempfile
from pathlib import Path
from tests.common import ROOT, FIX
from spm.model_service import ModelService, ModelLoadError


class ModelLoading(unittest.TestCase):
    def test_artifacts_load_and_match_manifest(self):
        m = ModelService()
        th = json.loads((ROOT / "model" / "thresholds.json").read_text())
        self.assertAlmostEqual(m.pipe.det.threshold_, th["threshold"], places=15)
        self.assertEqual((m.pipe.cfg.feature_set, m.pipe.cfg.k, m.pipe.cfg.level), ("abs", 20, "channel"))
        self.assertEqual(m.pipe.feature_names_, ["V_V", "I_mA"])
        self.assertEqual(len(m.pipe.det.ref_), 153834)

    def test_tampered_artifact_is_refused(self):
        d = Path(tempfile.mkdtemp()) / "model"; shutil.copytree(ROOT / "model", d)
        b = bytearray((d / "model_arrays.npz").read_bytes()); b[5000] ^= 0xFF; (d / "model_arrays.npz").write_bytes(bytes(b))
        with self.assertRaises(ModelLoadError) as cm: ModelService(d)
        self.assertIn("integrity", str(cm.exception).lower())

    def test_swapped_manifest_is_refused_by_pin(self):
        d = Path(tempfile.mkdtemp()) / "model"; shutil.copytree(ROOT / "model", d)
        man = json.loads((d / "manifest.json").read_text()); man["k"] = 5; (d / "manifest.json").write_text(json.dumps(man))
        with self.assertRaises(ModelLoadError): ModelService(d)

    def test_missing_artifact_is_reported(self):
        d = Path(tempfile.mkdtemp()) / "model"; shutil.copytree(ROOT / "model", d); (d / "thresholds.json").unlink()
        with self.assertRaises(ModelLoadError) as cm: ModelService(d)
        self.assertIn("missing", str(cm.exception).lower())


class Regression(unittest.TestCase):
    def test_model_package_is_byte_identical_to_release(self):
        want = json.loads((FIX / "package_hashes.json").read_text())
        have = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "cubesat_knn").glob("*.py"))}
        self.assertEqual(have, want)

    def test_model_files_match_release_checksums(self):
        sums = {}
        for line in (ROOT / "docs/release_docs/CHECKSUMS.txt").read_text().splitlines():
            if line and not line.startswith("#"): h, n = line.split(None, 1); sums[n.strip()] = h
        for n in ("config.json", "feature_schema.json", "model_arrays.npz", "thresholds.json", "provenance.json", "severity_baseline.json", "manifest.json"):
            self.assertEqual(hashlib.sha256((ROOT / "model" / n).read_bytes()).hexdigest(), sums[n], n)

    def test_app_never_imports_sklearn_or_refits(self):
        src = "".join(p.read_text() for p in (ROOT / "spm").glob("*.py"))
        for forbidden in (".fit(", "set_threshold", "retune(", "import pickle", "joblib", "allow_pickle=True", "random.rand", "np.random"):
            self.assertNotIn(forbidden, src, f"app code must not contain {forbidden!r}")


if __name__ == "__main__": unittest.main()
