"""HTTP layer. Thin by design: parsing lives in ingest.py, inference in model_service.py, statistics in results.py."""
from __future__ import annotations
import gzip, io, json, logging, logging.handlers, shutil, traceback, uuid
from pathlib import Path
import numpy as np, pandas as pd
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException

from . import __version__, settings, storage, ingest, results, exports
from .model_service import ModelService, ModelLoadError, IncompatibleBaselineError, FeatureSchemaError, SchemaError

log = logging.getLogger("spm")


def _setup_logging() -> None:
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); log.addHandler(sh)
    try:
        settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(settings.LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3); fh.setFormatter(fmt); log.addHandler(fh)
    except OSError:
        pass


def jresp(obj, status: int = 200) -> Response:
    return Response(json.dumps(results.to_jsonable(obj), allow_nan=False), status=status, mimetype="application/json")


def err(code: str, message: str, status: int = 400, details=None) -> Response:
    return jresp({"error": {"code": code, "message": message, "details": details}}, status)


def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


SAFE_TEXT_COLS = ("satellite", "session", "panel", "channel")


def csv_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Neutralise spreadsheet formula injection in text columns that originate from the uploaded file."""
    df = df.copy()
    for c in SAFE_TEXT_COLS:
        if c in df.columns:
            df[c] = df[c].map(lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v)
    return df


def create_app() -> Flask:
    _setup_logging()
    app = Flask(__name__, static_folder=str(settings.STATIC_DIR), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_MB * 1024 * 1024 + 4096
    app.config["JSON_SORT_KEYS"] = False
    model_error = None
    try:
        model: ModelService | None = ModelService()
        log.info("Model loaded: feature_set=%s k=%s threshold=%.6f", model.pipe.cfg.feature_set, model.pipe.cfg.k, model.pipe.det.threshold_)
        for w in model.version_warnings:
            log.warning(w)
    except ModelLoadError as e:
        model, model_error = None, str(e)
        log.error("MODEL NOT LOADED: %s", e)

    def need_model() -> ModelService:
        if model is None:
            abort(jresp({"error": {"code": "model_unavailable", "message": model_error}}, 503))
        return model

    # ---------------------------------------------------------------------------------------------- hooks
    @app.before_request
    def _housekeeping():
        storage.cleanup_expired()

    @app.after_request
    def _headers(resp: Response):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ingest.IngestError)
    def _ingest(e):
        return err(e.code, e.message, e.status, e.details)

    @app.errorhandler(results.AnalysisError)
    def _analysis(e):
        return err(e.code, e.message, e.status)

    @app.errorhandler(IncompatibleBaselineError)
    def _scale(e):
        return err("scale_guard", str(e), 422)

    @app.errorhandler(FeatureSchemaError)
    @app.errorhandler(SchemaError)
    def _schema(e):
        return err("schema", str(e), 422)

    @app.errorhandler(storage.RunNotFound)
    def _gone(e):
        return err("run_not_found", "This upload has expired or does not exist. Upload the file again.", 404)

    @app.errorhandler(RequestEntityTooLarge)
    def _big(e):
        return err("file_too_large", f"The file is larger than the {settings.MAX_UPLOAD_MB} MB limit.", 413)

    @app.errorhandler(404)
    def _nf(e):
        return err("not_found", "Not found.", 404)

    @app.errorhandler(405)
    def _na(e):
        return err("method_not_allowed", "Method not allowed.", 405)

    @app.errorhandler(Exception)
    def _unexpected(e):
        if isinstance(e, HTTPException) and e.response is not None:
            return e.response
        code = getattr(e, "code", None)
        if isinstance(code, int) and 400 <= code < 500:
            return err("http_error", getattr(e, "description", "Request error."), code)
        eid = uuid.uuid4().hex[:8]
        log.error("Unhandled error %s on %s\n%s", eid, request.path, traceback.format_exc())
        return err("internal_error", f"Unexpected server error (reference {eid}). The analysis was not completed and no results were produced.", 500)

    # ---------------------------------------------------------------------------------------------- pages / info
    @app.get("/")
    def index():
        r = send_from_directory(settings.STATIC_DIR, "index.html"); r.headers["Cache-Control"] = "no-cache"; return r

    @app.get("/api/health")
    def health():
        return jresp({"status": "ok" if model else "degraded", "model_loaded": model is not None, "error": model_error, "version": __version__})

    @app.get("/api/model")
    def model_info():
        m = need_model(); info = m.info()
        info["limits"] = {"max_upload_mb": settings.MAX_UPLOAD_MB, "max_observations": settings.MAX_OBSERVATIONS, "run_ttl_minutes": settings.RUN_TTL_SECONDS // 60,
                          "file_types": sorted(settings.ALLOWED_EXTENSIONS)}
        info["triage"] = {"min_scored": settings.TRIAGE_MIN_SCORED, "within_expected_max_pct": settings.TRIAGE_WITHIN_EXPECTED_MAX_PCT, "high_min_pct": settings.TRIAGE_HIGH_MIN_PCT}
        return jresp(info)

    # ---------------------------------------------------------------------------------------------- inspect
    def _json_field(name: str, source) -> dict | None:
        raw = source.get(name)
        if raw in (None, "", "null"):
            return None
        if isinstance(raw, dict):
            return raw
        try:
            val = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ingest.IngestError("bad_json", f"The {name} field is not valid JSON: {e.msg}.")
        if not isinstance(val, dict):
            raise ingest.IngestError("bad_json", f"The {name} field must be a JSON object.")
        return val

    def _do_inspect(token: str, rd: Path, meta: dict, mapping, units, satellite: str) -> Response:
        m = need_model()
        ext = meta["ext"]
        report = ingest.inspect_file(rd / f"upload{ext}", ext, meta["original_name"], m, satellite, mapping, units)
        meta.update({"mapping": mapping, "units": units, "satellite": satellite,
                     "sheet_warnings": {s["name"]: [m["text"] for m in s["messages"] if m["level"] == "warning"] for s in report["sheets"]}})
        storage.write_json(rd / "meta.json", meta)
        return jresp({"upload_id": token, "file": {"name": meta["original_name"], "size": meta["size"], "size_text": human_size(meta["size"]),
                                                    "type": ext.lstrip("."), "is_sample": meta.get("is_sample", False)},
                      "satellite": satellite, "inspect": report})

    @app.post("/api/inspect")
    def inspect_upload():
        need_model()
        f = request.files.get("file")
        if f is None or not f.filename:
            return err("no_file", "Choose a CSV or Excel file to upload.")
        name = Path(f.filename).name
        ext = Path(name).suffix.lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            return err("unsupported_type", f"Unsupported file type {ext or '(none)'}. Upload a .csv or .xlsx file (.xls is not supported; save it as .xlsx first).", 415)
        mapping, units = _json_field("mapping", request.form), _json_field("units", request.form)
        token, rd = storage.new_run()
        try:
            f.save(rd / f"upload{ext}")
            meta = {"original_name": name, "ext": ext, "size": (rd / f"upload{ext}").stat().st_size, "is_sample": False}
            satellite = ingest.clean_label(request.form.get("satellite", ""), ingest.clean_label(Path(name).stem, "UPLOAD"))
            return _do_inspect(token, rd, meta, mapping, units, satellite)
        except BaseException:
            shutil.rmtree(rd, ignore_errors=True); raise

    @app.post("/api/inspect/<token>")
    def reinspect(token):
        rd = storage.run_dir(token); meta = storage.read_json(rd / "meta.json")
        body = request.get_json(silent=True) or {}
        mapping, units = _json_field("mapping", body), _json_field("units", body)
        satellite = ingest.clean_label(body.get("satellite", ""), meta.get("satellite") or "UPLOAD")
        return _do_inspect(token, rd, meta, mapping, units, satellite)

    SAMPLE = settings.SAMPLE_DIR / "synthetic_benchmark_first_2.3_days.csv"

    @app.get("/api/sample")
    def sample_info():
        if not SAMPLE.exists():
            return err("no_sample", "No sample file is bundled.", 404)
        return jresp({"name": SAMPLE.name, "size_text": human_size(SAMPLE.stat().st_size),
                      "description": "Synthetic 4-panel benchmark (10 s cadence, 2.3 days). Faults were injected by a generator; it is not real satellite data."})

    @app.post("/api/sample")
    def use_sample():
        need_model()
        if not SAMPLE.exists():
            return err("no_sample", "No sample file is bundled.", 404)
        token, rd = storage.new_run()
        try:
            shutil.copyfile(SAMPLE, rd / "upload.csv")
            meta = {"original_name": SAMPLE.name, "ext": ".csv", "size": SAMPLE.stat().st_size, "is_sample": True}
            return _do_inspect(token, rd, meta, None, None, "SYNTHETIC_SAMPLE")
        except BaseException:
            shutil.rmtree(rd, ignore_errors=True); raise

    @app.delete("/api/runs/<token>")
    def delete_run(token):
        rd = storage.run_dir(token); shutil.rmtree(rd, ignore_errors=True)
        return jresp({"deleted": True})

    # ---------------------------------------------------------------------------------------------- analyse
    @app.post("/api/analyze")
    def analyze():
        m = need_model()
        body = request.get_json(silent=True) or {}
        token = body.get("upload_id")
        rd = storage.run_dir(token); meta = storage.read_json(rd / "meta.json")
        sheets = body.get("sheets")
        if not isinstance(sheets, list) or not sheets or not all(isinstance(s, str) for s in sheets):
            return err("no_sheets", "Select at least one sheet to analyse.")
        mapping = _json_field("mapping", body) if "mapping" in body else meta.get("mapping")
        units = _json_field("units", body) if "units" in body else meta.get("units")
        satellite = ingest.clean_label(body.get("satellite", ""), meta.get("satellite") or "UPLOAD")
        ext = meta["ext"]; path = rd / f"upload{ext}"
        try:
            tables = ingest.load_tables(path, ext, meta["original_name"], sheets, satellite, mapping, units)
            analysis = results.analyse(m, tables, meta["original_name"], satellite)
        except IncompatibleBaselineError as e:
            return err("scale_guard", f"Analysis refused: {e}", 422)
        except (FeatureSchemaError, SchemaError) as e:
            return err("schema", f"Analysis refused: {e}", 422)
        # drop any previous result for this upload only after the new one succeeded (never show stale results)
        tmp = rd / "result.tmp"; shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
        try:
            csv_safe(analysis.predictions).to_csv(tmp / "predictions.csv", index=False)
            csv_safe(analysis.intervals).to_csv(tmp / "intervals.csv", index=False)
            csv_safe(analysis.flagged).to_csv(tmp / "flagged.csv", index=False)
            for i, s in enumerate(analysis.summary["sessions"]):
                with gzip.open(tmp / f"series_{i}.json.gz", "wb", compresslevel=4) as g:
                    g.write(json.dumps(results.to_jsonable(analysis.series[s["name"]]), allow_nan=False, separators=(",", ":")).encode())
            sw = meta.get("sheet_warnings") or {}
            last = [f"{n}: {t}" for n in sheets for t in sw.get(n, [])]
            storage.write_json(tmp / "warnings.json", last)
            analysis.summary["input_size_text"] = human_size(meta["size"])
            analysis.summary["is_sample"] = bool(meta.get("is_sample"))
            storage.write_json(tmp / "summary.json", analysis.summary)
            old = rd / "result"
            if old.exists():
                shutil.rmtree(old)
            tmp.rename(old)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True); raise
        ov = analysis.summary["overview"]
        log.info("analysis ok id=%s sessions=%d observations=%d flagged=%d", token[:6], ov["sessions"], ov["observations"], ov["flagged_readings"])
        return jresp({"upload_id": token, "summary": analysis.summary})

    def _result_dir(token: str) -> Path:
        d = storage.run_dir(token) / "result"
        if not (d / "summary.json").exists():
            abort(jresp({"error": {"code": "no_result", "message": "No analysis exists for this upload yet."}}, 404))
        return d

    def _session_index(d: Path, name: str) -> int:
        summary = storage.read_json(d / "summary.json")
        for i, s in enumerate(summary["sessions"]):
            if s["name"] == name:
                return i
        abort(jresp({"error": {"code": "no_session", "message": "Unknown session."}}, 404))

    @app.get("/api/runs/<token>/summary")
    def get_summary(token):
        return jresp(storage.read_json(_result_dir(token) / "summary.json"))

    @app.get("/api/runs/<token>/series")
    def get_series(token):
        d = _result_dir(token); i = _session_index(d, request.args.get("session", ""))
        raw = (d / f"series_{i}.json.gz").read_bytes()
        if "gzip" in request.headers.get("Accept-Encoding", ""):
            r = Response(raw, mimetype="application/json"); r.headers["Content-Encoding"] = "gzip"
        else:
            r = Response(gzip.decompress(raw), mimetype="application/json")
        r.headers["Vary"] = "Accept-Encoding"
        return r

    @app.get("/api/runs/<token>/flagged")
    def get_flagged(token):
        d = _result_dir(token)
        df = pd.read_csv(d / "flagged.csv", dtype={"session": str, "panel": str})
        if (s := request.args.get("session")):
            df = df[df["session"] == s]
        if (p := request.args.get("panel")):
            df = df[df["panel"] == p]
        off = max(0, request.args.get("offset", 0, type=int)); lim = min(1000, max(1, request.args.get("limit", 200, type=int)))
        page = df.iloc[off:off + lim]
        return jresp({"total": int(len(df)), "offset": off, "rows": page.astype(object).where(page.notna(), None).to_dict(orient="records")})

    @app.get("/api/runs/<token>/intervals")
    def get_intervals(token):
        d = _result_dir(token)
        df = pd.read_csv(d / "intervals.csv", dtype={"session": str, "panel": str})
        if (s := request.args.get("session")):
            df = df[df["session"] == s]
        if (p := request.args.get("panel")):
            df = df[df["panel"] == p]
        total = len(df)
        df = df.sort_values(["flagged_readings", "max_score"], ascending=False).head(1000) if request.args.get("sort") == "size" else df.head(1000)
        return jresp({"total": int(total), "rows": df.astype(object).where(df.notna(), None).to_dict(orient="records")})

    # ---------------------------------------------------------------------------------------------- downloads
    def _chart_png(d: Path, session: str, summary: dict, min_run: int = 1) -> bytes:
        i = _session_index(d, session)
        cache = d / f"chart_{i}_{min_run}.png"
        if cache.exists():
            return cache.read_bytes()
        with gzip.open(d / f"series_{i}.json.gz", "rb") as g:
            series = json.loads(g.read())
        s = summary["sessions"][i]
        line = (f"Session {s['name']}: {s['flagged']:,} flagged readings in {s['intervals']:,} intervals across {len(s['panels'])} panels. "
                f"Model threshold {summary['threshold']:.4f} (kNN distance in scaled units).")
        png = exports.render_chart_png(series, line, min_run=min_run)
        cache.write_bytes(png)
        return png

    @app.get("/api/runs/<token>/download/<kind>")
    def download(token, kind):
        d = _result_dir(token); meta = storage.read_json(storage.run_dir(token) / "meta.json")
        stem = Path(meta["original_name"]).stem[:40] or "telemetry"
        stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
        summary = storage.read_json(d / "summary.json")
        if kind in ("predictions.csv", "intervals.csv", "flagged.csv"):
            return send_file(d / kind, mimetype="text/csv", as_attachment=True, download_name=f"{stem}_{kind}")
        session = request.args.get("session") or summary["sessions"][0]["name"]
        min_run = min(1000, max(1, request.args.get("min_run", 1, type=int)))
        if kind == "chart.png":
            return send_file(io.BytesIO(_chart_png(d, session, summary, min_run)), mimetype="image/png", as_attachment=True, download_name=f"{stem}_{session[:20]}_panel_voltages.png".replace(" ", "_"))
        if kind == "report.pdf":
            m = need_model(); info = m.info()
            info["threshold"] = summary["threshold"]
            iv = pd.read_csv(d / "intervals.csv", dtype={"session": str, "panel": str})
            pdf = exports.render_report_pdf(summary, info, iv, _chart_png(d, session, summary),
                                            {"name": meta["original_name"], "size_text": human_size(meta["size"])}, storage.read_json(d / "warnings.json"))
            return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=f"{stem}_report.pdf")
        abort(404)

    return app
