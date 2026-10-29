"""Browser test (optional; needs `pip install playwright` and `python -m playwright install chromium`).
Starts the app on a free port, drives it like a user, and checks what is actually drawn.   Run:  python tests/ui_smoke.py"""
import asyncio, json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.common import ROOT, _tmp, make_flat_csv, syn7_csv
import pandas as pd
from playwright.async_api import async_playwright

W = Path(_tmp); results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

async def red_pixels(pg, sel):
    return await pg.evaluate("""(sel) => { const c = document.querySelector(sel), x = c.getContext('2d'); const d = x.getImageData(0,0,c.width,c.height).data; let n = 0;
        for (let i = 0; i < d.length; i += 4) { if (d[i] > 150 && d[i+1] < 110 && d[i+2] < 120 && d[i+3] > 0) n++; } return n; }""", sel)

async def main():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PORT=str(port), SPM_DATA_DIR=str(W / "ui_runs"), SPM_LOG_DIR=str(W / "ui_logs"))
    srv = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try: urllib.request.urlopen(base + "/api/health", timeout=2); break
        except Exception: time.sleep(0.5)
    shots = Path(os.environ.get("SPM_SHOTS", W / "shots")); shots.mkdir(exist_ok=True, parents=True)
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(); pg = await b.new_page(viewport={"width": 1360, "height": 900})
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            await pg.goto(base); await pg.wait_for_selector("#modelChipText:has-text('Model ready')")
            check("header title and subtitle", await pg.inner_text("h1") == "Solar Panel Health Monitoring" and "Satellite Telemetry Analysis" in await pg.inner_text(".sub"))
            check("Run button disabled before a file is chosen", await pg.is_hidden("#runBar"))

            # --- unsupported file -> clear error, no results
            bad = W / "notes.txt"; bad.write_text("hello")
            await pg.set_input_files("#file", str(bad)); await pg.wait_for_selector("#runError:not([hidden])")
            check("unsupported type shows a clear error", "Unsupported file type" in await pg.inner_text("#runError") and await pg.is_hidden("#results"))

            # --- a real upload with the synthetic holdout slice that contains faults
            src = syn7_csv(W); df = pd.read_csv(src); sl = W / "ui_synthetic_2days.csv"; df[df["elapsed_seconds"] <= 120000].to_csv(sl, index=False)
            await pg.set_input_files("#file", str(sl)); await pg.wait_for_selector("#runBtn:not([disabled])", timeout=30000)
            check("validation summary shows panels and rows", "4 panels" in await pg.inner_text("#checkBody") and "12,001" in await pg.inner_text("#checkBody"))
            check("error banner cleared after valid file", await pg.is_hidden("#runError"))
            await pg.click("#runBtn"); await pg.wait_for_selector("#results:not([hidden])", timeout=90000); await pg.wait_for_timeout(1500)
            check("run chip says complete", "Analysis complete" in await pg.inner_text("#runChipText"))
            check("exact chart title", await pg.inner_text("#chartTitle") == "Panel Voltages (Red Band = Rows Flagged on the Faulty Panel)")
            check("4 panel cards", await pg.locator(".pcard").count() == 4)
            S = await pg.evaluate("window.__spm.summary"); ov = S["overview"]
            check("overview cards match API", (await pg.locator(".stat").nth(2).inner_text()).split("\n")[1].replace(",", "") == str(ov["flagged_readings"]))
            check("canvas draws red bands when the model flagged readings", await red_pixels(pg, "#cv") > 500 and await red_pixels(pg, "#cvLane") > 100, f"main={await red_pixels(pg,'#cv')} lane={await red_pixels(pg,'#cvLane')}")
            # hide every panel -> bands for hidden panels disappear (bands follow visible panels)
            for b_ in await pg.locator(".lg[aria-pressed]").all(): await b_.click()
            await pg.wait_for_timeout(300)
            check("hiding all panels hides their bands and shows a message", await red_pixels(pg, "#cvLane") == 0 and await pg.is_visible("#chartMsg"))
            for b_ in await pg.locator(".lg[aria-pressed]").all(): await b_.click()
            # zoom by dragging, then reset
            box = await pg.locator("#cv").bounding_box(); await pg.mouse.move(box["x"] + box["width"] * .3, box["y"] + 100); await pg.mouse.down(); await pg.mouse.move(box["x"] + box["width"] * .5, box["y"] + 100, steps=6); await pg.mouse.up()
            z = await pg.evaluate("window.__spm.chart.isZoomed()"); check("drag zoom works", z); await pg.click("#zReset"); check("reset zoom works", not await pg.evaluate("window.__spm.chart.isZoomed()"))
            await pg.mouse.move(box["x"] + box["width"] * .5, box["y"] + 120); await pg.wait_for_timeout(200); check("tooltip shows panel voltages", "V" in await pg.inner_text("#tip") and await pg.is_visible("#tip"))
            await pg.locator(".chart-card").screenshot(path=str(shots / "chart.png"))
            # interval filter changes drawing but not data
            before = await red_pixels(pg, "#cvLane"); await pg.click('button[data-minrun="10"]'); await pg.wait_for_timeout(300); after = await red_pixels(pg, "#cvLane")
            check("display filter reduces drawn bands and says so", after < before and "Display filter on" in await pg.inner_text("#chartNote"))
            await pg.click('button[data-minrun="1"]')
            # tabs + tables
            await pg.click("#tabIv"); await pg.wait_for_selector("#tblIv tbody tr"); check("intervals table populated", await pg.locator("#tblIv tbody tr").count() > 3)
            await pg.click("#tabRd"); await pg.wait_for_selector("#tblRd tbody tr"); check("flagged readings table populated", "of" in await pg.inner_text("#rdInfo"))
            await pg.click("#tabPanels"); await pg.fill("#fPanel", "P2"); check("panel filter works", await pg.locator("#tblPanels tbody tr").count() == 1); await pg.fill("#fPanel", "")
            # downloads are real files
            async with pg.expect_download() as d1: await pg.click("#dlPng")
            dl = await d1.value; path = await dl.path(); check("PNG download is a PNG", Path(path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
            async with pg.expect_download() as d2: await pg.click("#dlPred")
            dl = await d2.value; check("CSV download has the model columns", "score,threshold,anomaly" in Path(await dl.path()).read_text().splitlines()[0])
            # stale results are cleared when a new file is chosen
            await pg.set_input_files("#file", str(make_flat_csv(W / "ui_small.csv", n=300, panels=("A1", "A2")))); await pg.wait_for_selector("#runBtn:not([disabled])")
            check("old results are hidden as soon as a new file is chosen", await pg.is_hidden("#results"))
            await pg.click("#runBtn"); await pg.wait_for_selector("#results:not([hidden])", timeout=60000); await pg.wait_for_timeout(500)
            check("new results show the new file", await pg.locator(".pcard").count() == 2 and "ui_small.csv" in await pg.inner_text("#resultsMeta"))
            # --- a file whose scale is wrong is refused with a clear message
            await pg.set_input_files("#file", str(make_flat_csv(W / "ui_scale.csv", scale_i=1000.0))); await pg.wait_for_selector("#checkBody:not([hidden])")
            txt = await pg.inner_text("#checkBody"); check("scale mismatch refused in the UI", "cannot be applied" in txt and await pg.is_disabled("#runBtn"))
            # --- long-format file with ambiguous units asks the user
            w = pd.read_csv(make_flat_csv(W / "lw2.csv", n=200)); lg = pd.concat([pd.DataFrame({"elapsed_seconds": w["elapsed_seconds"], "panel_id": pn, "voltage": w[f"{pn}_voltage_V"], "current": w[f"{pn}_current_A"]}) for pn in ("P1", "P2")])
            lg.to_csv(W / "long_ui.csv", index=False); await pg.set_input_files("#file", str(W / "long_ui.csv")); await pg.wait_for_selector(".needs")
            check("ambiguous units are requested", await pg.locator(".needs select").count() == 2)
            sels = pg.locator(".needs select"); await sels.nth(0).select_option("V"); await sels.nth(1).select_option("A"); await pg.click(".needs button"); await pg.wait_for_selector("#runBtn:not([disabled])")
            check("after choosing units the file is accepted", "2 panels" in await pg.inner_text("#checkBody"))
            # --- mobile layout
            await pg.set_viewport_size({"width": 390, "height": 844}); await pg.wait_for_timeout(400)
            ov = await pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"); check("no horizontal page scroll on a phone", ov)
            check("no uncaught page errors", not errs, "; ".join(errs[:3]))
            await b.close()
    finally:
        srv.terminate(); srv.wait(10)
    bad = [n for n, ok in results if not ok]; print(f"\n{len(results) - len(bad)}/{len(results)} browser checks passed"); sys.exit(1 if bad else 0)

asyncio.run(main())
