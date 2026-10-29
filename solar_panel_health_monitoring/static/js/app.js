/* Solar Panel Health Monitoring - front end. All user-supplied text is inserted with textContent (never innerHTML). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const S = { upload: null, inspect: null, selected: new Set(), units: {}, mapping: null, summary: null, session: null, series: null,
              hidden: new Set(), minRun: 1, chart: null, model: null, busy: false, rd: { offset: 0, total: 0 }, sort: { key: "panel", dir: 1 } };

  /* ------------------------------------------------------------------ helpers */
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat()) { if (c == null || c === false) continue; e.append(c.nodeType ? c : document.createTextNode(String(c))); }
    return e;
  }
  function svg(path, size, cls) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 16 16"); s.setAttribute("width", size || 14); s.setAttribute("height", size || 14); s.setAttribute("aria-hidden", "true");
    if (cls) s.setAttribute("class", cls); s.innerHTML = path; return s;   // static markup only
  }
  const ICON = {
    check: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 8.3l2.2 2.2L11 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    tri: '<path d="M8 2l6.5 11.5h-13z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 6.5v3.2M8 11.4v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    oct: '<path d="M5.2 1.8h5.6l3.4 3.4v5.6l-3.4 3.4H5.2L1.8 10.8V5.2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M8 5v3.8M8 11v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    dash: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 8h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    info: '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 7.2v4M8 4.8v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  };
  const fInt = (n) => n == null ? "n/a" : Number(n).toLocaleString("en-US");
  const fPct = (n, d) => n == null ? "n/a" : Number(n).toFixed(d == null ? 2 : d) + " %";
  const fV = (n) => n == null ? "n/a" : Number(n).toFixed(3);
  const fScore = (n) => n == null ? "n/a" : Number(n).toFixed(4);
  const fEl = (t) => window.SPM_COLORS.fmtElapsed(t, 1e9);
  function fDur(s) { s = Math.round(s); if (s < 60) return s + " s"; if (s < 3600) return Math.floor(s / 60) + " min " + (s % 60) + " s"; if (s < 86400) return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min"; return Math.floor(s / 86400) + " d " + Math.floor((s % 86400) / 3600) + " h"; }

  const TRI = { green: ["badge-green", "check", "c-green"], amber: ["badge-amber", "tri", "c-amber"], red: ["badge-red", "oct", "c-red"], gray: ["badge-gray", "dash", "c-gray"] };
  function badge(t) { const [cls, ic] = TRI[t.color]; return h("span", { class: "badge " + cls }, svg(ICON[ic], 13), t.label); }

  async function api(url, opts) {
    let r;
    try { r = await fetch(url, opts); } catch (e) { throw { code: "network", message: "Could not reach the server. Check that the application is running and try again." }; }
    let body = null; try { body = await r.json(); } catch (e) { /* non-JSON */ }
    if (!r.ok) { const er = (body && body.error) || { code: "http_" + r.status, message: "The server returned an error (" + r.status + ")." }; throw er; }
    return body;
  }
  function setChip(id, textId, cls, text, busy) { const c = $(id); c.className = "chip chip-" + cls + (busy ? " chip-busy" : ""); $(textId).textContent = text; }
  const setRun = (cls, text, busy) => setChip("runChip", "runChipText", cls, text, busy);

  function showError(e, where) {
    const box = where || $("runError"); box.textContent = ""; box.hidden = false;
    box.append(h("strong", { text: "Could not complete this step. " }), e.message || "Unknown error.");
    if (e.details && Array.isArray(e.details)) box.append(h("div", { class: "detail" }, e.details.slice(0, 6).map((d) => h("div", { text: (d.sheet ? d.sheet + ": " : "") + (d.problem || "") }))));
    else if (e.details && e.details.columns) box.append(h("div", { class: "detail", text: "Columns found: " + e.details.columns.join(", ") }));
    setRun("bad", "Needs attention");
  }
  const clearError = () => { $("runError").hidden = true; };

  /* ------------------------------------------------------------------ model info */
  async function loadModel() {
    try {
      const m = await api("/api/model"); S.model = m;
      setChip("modelChip", "modelChipText", "ok", "Model ready: " + m.feature_set + " features, k = " + m.k);
      $("maxMb").textContent = m.limits.max_upload_mb; $("ttl").textContent = Math.round(m.limits.run_ttl_minutes / 60 * 10) / 10;
      $("trA").textContent = m.triage.within_expected_max_pct; $("trB").textContent = m.triage.high_min_pct;
      const dl = $("modelFacts"); dl.textContent = "";
      const rows = [["Algorithm", "k-nearest-neighbour distance (k = " + m.k + ", " + m.scaler + " scaling)"], ["Inputs per reading", m.features.join(", ")],
        ["Threshold", m.threshold.toFixed(6) + " (" + m.threshold_percentile + "th percentile of reference scores)"], ["Reference data", fInt(m.reference_rows) + " readings, " + m.reference_sessions + " sessions"],
        ["Reference satellites", Object.entries(m.reference_by_satellite || {}).map(([k, v]) => k + " " + fInt(v)).join(", ")], ["Scale guard", "refuses data whose V or I scale is outside " + (1 / m.scale_guard.max_scale_ratio) + "x to " + m.scale_guard.max_scale_ratio + "x of the reference"],
        ["Model files", "package " + m.package_version + ", manifest " + m.manifest_sha256]];
      for (const [k, v] of rows) dl.append(h("dt", { text: k }), h("dd", { text: v }));
      if (m.version_warnings && m.version_warnings.length) { $("banner").hidden = false; $("banner").textContent = m.version_warnings.join(" "); }
    } catch (e) {
      setChip("modelChip", "modelChipText", "bad", "Model unavailable"); $("banner").hidden = false; $("banner").textContent = e.message || "The model could not be loaded."; $("drop").style.opacity = .5; S.noModel = true;
    }
  }
  $("aboutBtn").addEventListener("click", () => { const p = $("aboutPanel"); p.hidden = !p.hidden; $("aboutBtn").setAttribute("aria-expanded", String(!p.hidden)); });

  /* ------------------------------------------------------------------ upload */
  const drop = $("drop"), fileIn = $("file");
  drop.addEventListener("click", () => !S.busy && fileIn.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileIn.click(); } });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) startUpload(f); });
  fileIn.addEventListener("change", () => { if (fileIn.files[0]) startUpload(fileIn.files[0]); fileIn.value = ""; });
  $("sampleBtn").addEventListener("click", async () => {
    if (S.busy || S.noModel) return; beginInspect(); setRun("blue", "Checking sample", true);
    try { applyInspect(await api("/api/sample", { method: "POST" })); } catch (e) { showError(e); endInspect(); }
  });

  function resetAll(keepFile) {
    clearError(); hideResults(); S.inspect = null; if (!keepFile) { S.upload = null; S.units = {}; S.mapping = null; $("mapText").value = ""; }
    $("checkBody").hidden = true; $("checkEmpty").hidden = false; $("runBar").hidden = true; $("progress").hidden = true;
  }
  function beginInspect() { S.busy = true; resetAll(false); $("progress").hidden = false; $("barFill").classList.remove("indet"); $("barFill").style.width = "0%"; $("progressText").textContent = "Starting upload"; }
  function endInspect() { S.busy = false; $("progress").hidden = true; }

  function startUpload(file) {
    if (S.busy || S.noModel) return;
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (![".csv", ".xlsx", ".xlsm"].includes(ext)) { resetAll(false); showError({ message: "Unsupported file type " + (ext || "(none)") + ". Upload a .csv or .xlsx file. For .xls, save the workbook as .xlsx first." }); return; }
    const max = (S.model ? S.model.limits.max_upload_mb : 25) * 1048576;
    if (file.size > max) { resetAll(false); showError({ message: "This file is " + (file.size / 1048576).toFixed(1) + " MB; the limit is " + (max / 1048576) + " MB." }); return; }
    beginInspect(); setRun("blue", "Uploading", true);
    const fd = new FormData(); fd.append("file", file); fd.append("satellite", $("satLabel").value);
    const x = new XMLHttpRequest(); x.open("POST", "/api/inspect");
    x.upload.onprogress = (e) => { if (e.lengthComputable) { const p = Math.round(e.loaded / e.total * 100); $("barFill").style.width = p + "%"; $("progressText").textContent = "Uploading " + file.name + " (" + p + " %)"; } };
    x.upload.onload = () => { $("barFill").classList.add("indet"); $("progressText").textContent = "Checking columns, units and data quality"; setRun("blue", "Checking file", true); };
    x.onload = () => {
      let body = null; try { body = JSON.parse(x.responseText); } catch (e) { /* ignore */ }
      if (x.status === 200) applyInspect(body); else { showError((body && body.error) || { message: "Upload failed (" + x.status + ")." }); endInspect(); }
    };
    x.onerror = () => { showError({ message: "The upload failed. Check your connection and that the application is running." }); endInspect(); };
    x.send(fd);
  }

  function applyInspect(res) {
    endInspect(); clearError(); S.upload = res; S.inspect = res.inspect; S.selected = new Set(res.inspect.usable_sheets);
    $("satLabel").value = res.satellite || ""; renderCheck(); $("runBar").hidden = false; updateRunState();
    const n = res.inspect.sheets.length, ok = res.inspect.usable_sheets.length, need = res.inspect.sheets.some((s) => s.status === "needs_input");
    if (ok) setRun("blue", "File checked"); else if (need) setRun("warn", "Units needed"); else setRun("bad", "No usable data");
  }

  function msgList(msgs) {
    return h("ul", { class: "msgs" }, msgs.map((m) => h("li", { class: "msg msg-" + m.level },
      svg(m.level === "error" ? ICON.oct : m.level === "warning" ? ICON.tri : ICON.info, 15), h("span", {}, h("b", { text: m.level === "error" ? "Problem: " : m.level === "warning" ? "Warning: " : "Note: " }), m.text))));
  }

  function renderCheck() {
    const R = S.upload, I = S.inspect, body = $("checkBody"); body.textContent = ""; $("checkEmpty").hidden = true; body.hidden = false;
    body.append(h("div", { class: "file-line" }, h("span", { class: "file-name", text: R.file.name }), h("span", { class: "tag", text: R.file.size_text }), h("span", { class: "tag", text: R.file.type.toUpperCase() }),
      R.file.is_sample ? h("span", { class: "tag", text: "Synthetic sample" }) : null));
    const single = I.sheets.length === 1, usable = I.sheets.filter((s) => s.selectable);
    if (single) {
      const s = I.sheets[0];
      if (s.status === "ok") body.append(h("p", { class: "small" }, h("strong", { text: s.panels.length + " panels" }), " (" + s.panels.slice(0, 12).join(", ") + (s.panels.length > 12 ? " ..." : "") + "), ", h("strong", { text: fInt(s.rows) }), " timestamps",
        s.quality.cadence.median_s ? ", median cadence " + s.quality.cadence.median_s + " s" : "", "."));
      body.append(msgList(s.messages));
      if (s.unit_conversions && s.unit_conversions.length && s.status === "ok") body.append(h("details", { class: "adv" }, h("summary", { text: "Unit conversions applied (" + s.unit_conversions.length + ")" }), h("ul", { class: "plain small muted" }, s.unit_conversions.map((u) => h("li", { text: u })))));
      if (s.scale && s.scale.length) body.append(h("div", { class: "scale" }, s.scale.map((c) => h("span", { text: (c.feature === "V_V" ? "Voltage" : "Current") + " scale vs reference: " + (c.ratio == null ? "n/a" : c.ratio.toFixed(2) + "x") + (c.ok ? " (accepted)" : " (outside range)") }))));
      if (s.needs_input && s.needs_input.length) body.append(needsForm(s.needs_input));
    } else {
      body.append(h("p", { class: "small" }, h("strong", { text: usable.length + " of " + I.sheets.length + " sheets" }), " can be analysed. Each sheet is treated as one session."));
      const all = h("input", { type: "checkbox", "aria-label": "Select all usable sheets", checked: usable.length > 0 && usable.every((s) => S.selected.has(s.name)) });
      all.addEventListener("change", () => { S.selected = new Set(all.checked ? usable.map((s) => s.name) : []); renderChecks(); sel(); });
      const find = h("input", { type: "search", placeholder: "Find sheet", "aria-label": "Find sheet" });
      const list = h("div", { class: "sheets-scroll" });
      const renderChecks = () => { list.textContent = ""; const q = find.value.trim().toLowerCase();
        for (const s of I.sheets) { if (q && !s.name.toLowerCase().includes(q)) continue;
          const cb = h("input", { type: "checkbox", disabled: !s.selectable, checked: S.selected.has(s.name), "aria-label": "Analyse sheet " + s.name });
          cb.addEventListener("change", () => { cb.checked ? S.selected.add(s.name) : S.selected.delete(s.name); all.checked = usable.every((u) => S.selected.has(u.name)); sel(); });
          const bad = s.messages.filter((m) => m.level === "error"), warn = s.messages.filter((m) => m.level === "warning");
          list.append(h("div", { class: "sheet" + (s.selectable ? "" : " bad") }, cb, h("span", { class: "nm", title: s.name, text: s.name }), h("span", { class: "rw muted", text: fInt(s.rows) + " rows" }),
            h("span", { class: "pn", text: s.panels && s.panels.length ? s.panels.length + " panels" : "" }),
            s.selectable ? (warn.length ? h("span", { class: "badge badge-amber" }, svg(ICON.tri, 12), warn.length + (warn.length > 1 ? " warnings" : " warning")) : h("span", { class: "badge badge-green" }, svg(ICON.check, 12), "Ready"))
              : h("span", { class: "badge " + (s.status === "needs_input" ? "badge-amber" : "badge-red") }, svg(s.status === "needs_input" ? ICON.tri : ICON.oct, 12), s.status === "needs_input" ? "Units needed" : "Not usable"),
            (bad.length || warn.length) ? h("div", { class: "sheet-msgs" }, (bad.length ? bad : warn).slice(0, 2).map((m) => h("div", { text: m.text }))) : null)); } };
      const sel = () => { updateRunState(); hideResults(); };
      body.append(h("div", { class: "sheets" }, h("div", { class: "sheets-tools" }, h("label", { class: "field inline" }, all, h("span", { text: "Select all usable" })), I.sheets.length > 8 ? find : null), list));
      find.addEventListener("input", renderChecks); renderChecks();
      const need = I.sheets.find((s) => s.needs_input && s.needs_input.length); if (need) body.append(needsForm(need.needs_input));
    }
  }
  function needsForm(fields) {
    const wrap = h("div", { class: "needs" }); const picks = {};
    for (const f of fields) { const sel = h("select", { "aria-label": f.label }, h("option", { value: "", text: "Choose" }), f.options.map((o) => h("option", { value: o, text: o }))); picks[f.field] = sel; wrap.append(h("label", { class: "field" }, h("span", { text: f.label }), sel)); }
    const b = h("button", { class: "btn btn-ghost", type: "button", text: "Apply units" });
    b.addEventListener("click", async () => {
      const u = {}; for (const [k, s] of Object.entries(picks)) { if (!s.value) { showError({ message: "Choose a unit for every field before applying." }); return; } u[k] = s.value; }
      S.units = Object.assign(S.units, u); await reinspect();
    });
    wrap.append(b); return wrap;
  }
  async function reinspect() {
    if (S.busy) return; S.busy = true; clearError(); hideResults(); setRun("blue", "Checking file", true);
    try { const r = await api("/api/inspect/" + S.upload.upload_id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ units: S.units, mapping: S.mapping, satellite: $("satLabel").value }) }); S.busy = false; applyInspect(r); }
    catch (e) { S.busy = false; showError(e); }
  }
  $("mapApply").addEventListener("click", () => {
    const t = $("mapText").value.trim(); if (!t) { S.mapping = null; return reinspect(); }
    try { const m = JSON.parse(t); if (typeof m !== "object" || Array.isArray(m)) throw new Error(); S.mapping = m; } catch (e) { showError({ message: "The column mapping must be a valid JSON object." }); return; }
    reinspect();
  });
  function updateRunState() { $("runBtn").disabled = S.busy || S.selected.size === 0; }

  /* ------------------------------------------------------------------ run analysis */
  $("runBtn").addEventListener("click", async () => {
    if (S.busy || !S.upload) return; S.busy = true; clearError(); hideResults(); $("runBtn").disabled = true;
    $("progress").hidden = false; $("barFill").classList.add("indet"); setRun("blue", "Analysing", true);
    const obs = S.inspect.sheets.filter((s) => S.selected.has(s.name)).reduce((a, s) => a + (s.rows || 0), 0);
    $("progressText").textContent = "Scoring about " + fInt(obs) + " timestamps with the model. Large files can take a minute.";
    try {
      const r = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ upload_id: S.upload.upload_id, sheets: [...S.selected], satellite: $("satLabel").value, mapping: S.mapping, units: S.units }) });
      S.summary = r.summary; setRun("ok", "Analysis complete"); await showResults();
    } catch (e) { showError(e); setRun("bad", "Analysis failed"); }
    finally { S.busy = false; $("progress").hidden = true; $("barFill").classList.remove("indet"); updateRunState(); }
  });
  function hideResults() { $("results").hidden = true; S.summary = null; S.series = null; if (S.chart) { S.chart.destroy(); S.chart = null; } }

  /* ------------------------------------------------------------------ results */
  async function showResults() {
    const sm = S.summary, ov = sm.overview; $("results").hidden = false;
    $("resultsMeta").textContent = sm.input_name + " (" + sm.input_size_text + ")" + (sm.is_sample ? ", synthetic sample" : "") + ". Model threshold " + sm.threshold.toFixed(4) + ".";
    const stat = (lbl, val, cap, icon) => h("div", { class: "stat", role: "listitem" }, h("div", { class: "lbl", text: lbl }), h("div", { class: "val" }, icon || null, val), h("div", { class: "cap", text: cap }));
    const ovEl = $("overview"); ovEl.textContent = "";
    ovEl.append(
      stat("Panels analysed", String(ov.panels), ov.panel_names.slice(0, 8).join(", ") + (ov.panel_names.length > 8 ? " ..." : "")),
      stat("Telemetry records", fInt(ov.observations), ov.sessions > 1 ? "timestamps across " + ov.sessions + " sessions" : "timestamps, " + fInt(ov.panel_readings) + " panel readings"),
      stat("Flagged readings", fInt(ov.flagged_readings), "of " + fInt(ov.scored_readings) + " scored panel readings" + (ov.unscorable_readings ? "; " + fInt(ov.unscorable_readings) + " unscorable" : "")),
      stat("Flagged share", fPct(ov.flagged_pct_of_scored, 2), "of scored readings. Not a damage probability."),
      stat("Analysis status", "Complete", "model " + (S.model ? S.model.feature_set + ", k = " + S.model.k : ""), h("span", { style: "color:var(--ok);display:inline-flex" }, svg(ICON.check, 22))));
    const sel = $("sessionSel"); sel.textContent = "";
    for (const s of sm.sessions) sel.append(h("option", { value: s.name, text: s.name + (s.flagged_pct != null ? " (" + s.flagged_pct.toFixed(1) + " % flagged)" : "") }));
    $("sessionWrap").hidden = sm.sessions.length < 2; S.session = sm.sessions[0].name; sel.value = S.session; S.hidden = new Set(); S.minRun = 1;
    document.querySelectorAll("[data-minrun]").forEach((b) => { const on = b.dataset.minrun === "1"; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
    if (!S.chart) S.chart = new VoltageChart({ canvas: $("cv"), lane: $("cvLane"), tip: $("tip"), msg: $("chartMsg"), box: $("chartBox"), status: $("chartStatus"), onView: (z) => { $("zReset").disabled = !z; } });
    await selectSession();
    $("results").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); $("resultsTitle").focus({ preventScroll: true });
  }
  $("sessionSel").addEventListener("change", (e) => { S.session = e.target.value; S.hidden = new Set(); selectSession(); });

  const curSession = () => S.summary.sessions.find((s) => s.name === S.session);
  async function selectSession() {
    const s = curSession(), id = S.upload.upload_id, q = encodeURIComponent(S.session);
    renderPanelCards(s); renderLegend(s); fillSelectors(s); renderPanelTable(); updateLinks();
    $("chartMsg").hidden = false; $("chartMsg").textContent = "Loading voltage series";
    try { S.series = await api("/api/runs/" + id + "/series?session=" + q); S.chart.setHidden(S.hidden); S.chart.setMinRun(S.minRun); S.chart.setData(S.series); $("chartMsg").hidden = true; updateChartNote(); }
    catch (e) { $("chartMsg").textContent = e.message; }
    loadIntervals(); S.rd.offset = 0; loadReadings();
  }
  function updateChartNote() {
    const s = curSession(), t = S.series, shown = S.series.intervals.filter((i) => i.n >= S.minRun).length; const bits = [];
    bits.push("Session " + s.name + ": " + fInt(s.flagged) + " flagged readings in " + fInt(s.intervals) + " intervals.");
    if (S.minRun > 1) bits.push("Display filter on: drawing " + fInt(shown) + " intervals of " + S.minRun + "+ consecutive readings. All flags remain in the CSV and tables.");
    if (t.decimated) bits.push("Long file: " + fInt(t.n_sent) + " of " + fInt(t.n_total) + " readings are drawn (min/max per bucket, every flagged reading kept). Statistics use all readings.");
    if (t.iso_start) bits.push("First sample: " + t.iso_start + " UTC.");
    if (s.unplottable_readings) bits.push(fInt(s.unplottable_readings) + " readings without a usable timestamp are scored but not drawn.");
    $("chartNote").textContent = bits.join(" ");
  }
  document.querySelectorAll("[data-minrun]").forEach((b) => b.addEventListener("click", () => {
    S.minRun = +b.dataset.minrun; document.querySelectorAll("[data-minrun]").forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", String(on)); });
    if (S.chart && S.series) { S.chart.setMinRun(S.minRun); updateChartNote(); } updateLinks();
  }));
  $("zIn").addEventListener("click", () => S.chart && S.chart.zoomFactor(0.5)); $("zOut").addEventListener("click", () => S.chart && S.chart.zoomFactor(2)); $("zReset").addEventListener("click", () => S.chart && S.chart.reset());

  function renderLegend(s) {
    const lg = $("legend"); lg.textContent = "";
    s.panels.forEach((p, k) => { const b = h("button", { type: "button", class: "lg", "aria-pressed": "true", title: "Show or hide " + p.panel }, h("i", { style: "background:" + window.SPM_COLORS.lineColor(k) }), p.panel);
      b.addEventListener("click", () => { const off = !S.hidden.has(p.panel); off ? S.hidden.add(p.panel) : S.hidden.delete(p.panel); b.setAttribute("aria-pressed", String(!off)); S.chart && S.chart.setHidden(S.hidden); }); lg.append(b); });
    lg.append(h("span", { class: "lg lg-flag" }, h("i"), "Flagged by model"));
  }

  function renderPanelCards(s) {
    $("panelsTitle").textContent = "Panels" + (S.summary.sessions.length > 1 ? " in " + s.name : ""); const box = $("panelCards"); box.textContent = "";
    for (const p of s.panels) {
      const c = TRI[p.triage.color][2], w = p.flagged_pct == null ? 0 : Math.min(100, p.flagged_pct / 25 * 100);
      box.append(h("article", { class: "pcard " + c, "aria-label": "Panel " + p.panel },
        h("div", { class: "pcard-top" }, h("span", { class: "pname", title: p.panel, text: p.panel }), badge(p.triage)),
        h("div", { class: "pv" }, p.median_v == null ? "n/a" : p.median_v.toFixed(2), h("small", { text: p.median_v == null ? "" : "V median" })),
        h("div", { class: "meter", title: "Flagged share on a 0 to 25 % scale", role: "img", "aria-label": "Flagged share " + fPct(p.flagged_pct) }, h("i", { style: "width:" + w + "%" })),
        h("dl", {}, h("dt", { text: "Readings analysed" }), h("dd", { text: fInt(p.scored) }), h("dt", { text: "Flagged readings" }), h("dd", { text: fInt(p.flagged) }), h("dt", { text: "Flagged share" }), h("dd", { text: fPct(p.flagged_pct) }),
          h("dt", { text: "Intervals" }), h("dd", { text: fInt(p.intervals) }))));
    }
  }

  /* tables */
  function fillSelectors(s) {
    const fs = $("fStatus"); fs.textContent = ""; fs.append(h("option", { value: "", text: "All" })); [...new Set(s.panels.map((p) => p.triage.label))].forEach((l) => fs.append(h("option", { value: l, text: l })));
    for (const id of ["ivPanel", "rdPanel"]) { const e = $(id); e.textContent = ""; e.append(h("option", { value: "", text: "All" })); s.panels.forEach((p) => e.append(h("option", { value: p.panel, text: p.panel }))); }
    $("fPanel").value = "";
  }
  const COLS = [["panel", "Panel", (p) => p.panel, "l"], ["samples", "Readings", (p) => fInt(p.samples)], ["scored", "Scored", (p) => fInt(p.scored)], ["flagged", "Flagged", (p) => fInt(p.flagged)],
    ["flagged_pct", "Flagged %", (p) => p.flagged_pct == null ? "n/a" : p.flagged_pct.toFixed(2)], ["intervals", "Intervals", (p) => fInt(p.intervals)], ["median_v", "Median V", (p) => fV(p.median_v)],
    ["max_score", "Max score", (p) => fScore(p.max_score)], ["status", "Status", null, "l"], ["notes", "Data-quality notes", null, "notes"]];
  function renderPanelTable() {
    const s = curSession(), t = $("tblPanels"), fs = $("fStatus").value, fp = $("fPanel").value.trim().toLowerCase();
    let rows = s.panels.filter((p) => (!fs || p.triage.label === fs) && (!fp || p.panel.toLowerCase().includes(fp)));
    const k = S.sort.key; rows = rows.slice().sort((a, b) => { const g = (p) => k === "status" ? p.triage.label : k === "notes" ? p.notes.length : p[k]; const x = g(a), y = g(b); return (x == null ? -1 : y == null ? 1 : x < y ? -1 : x > y ? 1 : 0) * S.sort.dir; });
    t.textContent = ""; const hr = h("tr");
    for (const [key, lbl, , cls] of COLS) { const th = h("th", { scope: "col", class: cls === "l" || cls === "notes" ? "l" : "", "aria-sort": S.sort.key === key ? (S.sort.dir > 0 ? "ascending" : "descending") : "none" },
      h("button", { type: "button" }, lbl, S.sort.key === key ? (S.sort.dir > 0 ? " \u25B2" : " \u25BC") : "")); th.firstChild.addEventListener("click", () => { S.sort = { key, dir: S.sort.key === key ? -S.sort.dir : 1 }; renderPanelTable(); }); hr.append(th); }
    t.append(h("thead", {}, hr)); const tb = h("tbody");
    if (!rows.length) tb.append(h("tr", {}, h("td", { class: "empty", colspan: COLS.length, text: "No panels match this filter." }))); 
    for (const p of rows) tb.append(h("tr", {}, COLS.map(([key, , fn, cls]) => key === "status" ? h("td", { class: "l" }, badge(p.triage)) : key === "notes" ? h("td", { class: "notes", text: p.notes.length ? p.notes.join("; ") : "None" }) : h("td", { class: cls === "l" ? "l" : "", text: fn(p) }))));
    t.append(tb);
  }
  $("fStatus").addEventListener("change", renderPanelTable); $("fPanel").addEventListener("input", renderPanelTable);

  async function loadIntervals() {
    const id = S.upload.upload_id, p = $("ivPanel").value, t = $("tblIv");
    try {
      const r = await api("/api/runs/" + id + "/intervals?sort=size&session=" + encodeURIComponent(S.session) + (p ? "&panel=" + encodeURIComponent(p) : ""));
      $("ivInfo").textContent = r.total ? "Showing the " + Math.min(r.total, r.rows.length) + " longest of " + fInt(r.total) + " intervals. Choose Zoom to inspect one on the chart." : "";
      t.textContent = ""; t.append(h("thead", {}, h("tr", {}, ["Panel", "Start", "End", "Duration", "Readings", "Max score", "Dominant diagnosis", ""].map((x, i) => h("th", { scope: "col", class: i === 0 || i === 6 ? "l" : "", text: x })))));
      const tb = h("tbody"); if (!r.rows.length) tb.append(h("tr", {}, h("td", { class: "empty", colspan: 8, text: "The model flagged no readings for this selection." })));
      for (const v of r.rows) { const z = h("button", { type: "button", class: "rowbtn", text: "Zoom" }); z.addEventListener("click", () => { if (S.chart) { S.chart.focusInterval(v.start_t_s, v.end_t_s); $("chartTitle").scrollIntoView({ behavior: "smooth", block: "start" }); } });
        tb.append(h("tr", {}, h("td", { class: "l", text: v.panel }), h("td", { text: fEl(v.start_t_s) }), h("td", { text: fEl(v.end_t_s) }), h("td", { text: fDur(v.duration_s) }), h("td", { text: fInt(v.flagged_readings) }), h("td", { text: fScore(v.max_score) }), h("td", { class: "l", text: String(v.dominant_diagnosis).replace(/_/g, " ") }), h("td", {}, z))); }
      t.append(tb);
    } catch (e) { t.textContent = ""; t.append(h("tbody", {}, h("tr", {}, h("td", { class: "empty", text: e.message })))); }
  }
  $("ivPanel").addEventListener("change", loadIntervals);

  async function loadReadings() {
    const id = S.upload.upload_id, p = $("rdPanel").value, t = $("tblRd"), L = 100;
    try {
      const r = await api("/api/runs/" + id + "/flagged?limit=" + L + "&offset=" + S.rd.offset + "&session=" + encodeURIComponent(S.session) + (p ? "&panel=" + encodeURIComponent(p) : ""));
      S.rd.total = r.total; $("rdInfo").textContent = r.total ? "Readings " + fInt(r.offset + 1) + " to " + fInt(r.offset + r.rows.length) + " of " + fInt(r.total) : "";
      $("rdPrev").disabled = S.rd.offset === 0; $("rdNext").disabled = S.rd.offset + L >= r.total;
      t.textContent = ""; t.append(h("thead", {}, h("tr", {}, ["Panel", "Elapsed", "Voltage (V)", "Current (mA)", "Score", "Threshold", "Diagnosis (model wording)"].map((x, i) => h("th", { scope: "col", class: i === 0 || i === 6 ? "l" : "", text: x })))));
      const tb = h("tbody"); if (!r.rows.length) tb.append(h("tr", {}, h("td", { class: "empty", colspan: 7, text: "No flagged readings for this selection." })));
      for (const v of r.rows) tb.append(h("tr", {}, h("td", { class: "l", text: v.panel }), h("td", { text: v.t_s == null ? "n/a" : fEl(v.t_s) }), h("td", { text: fV(v.V_V) }), h("td", { text: v.I_mA == null ? "n/a" : Number(v.I_mA).toFixed(1) }), h("td", { text: fScore(v.score) }), h("td", { text: fScore(v.threshold) }), h("td", { class: "l", text: String(v.diagnosis || "").replace(/_/g, " ") })));
      t.append(tb);
    } catch (e) { t.textContent = ""; t.append(h("tbody", {}, h("tr", {}, h("td", { class: "empty", text: e.message })))); }
  }
  $("rdPanel").addEventListener("change", () => { S.rd.offset = 0; loadReadings(); });
  $("rdPrev").addEventListener("click", () => { S.rd.offset = Math.max(0, S.rd.offset - 100); loadReadings(); });
  $("rdNext").addEventListener("click", () => { S.rd.offset += 100; loadReadings(); });

  /* tabs */
  const TABS = [["tabPanels", "paneP"], ["tabIv", "paneI"], ["tabRd", "paneR"]];
  TABS.forEach(([tid], i) => {
    const t = $(tid);
    t.addEventListener("click", () => activate(i));
    t.addEventListener("keydown", (e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const n = (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length; activate(n); $(TABS[n][0]).focus(); } });
  });
  function activate(i) { TABS.forEach(([tid, pid], j) => { const on = i === j; $(tid).setAttribute("aria-selected", String(on)); $(tid).tabIndex = on ? 0 : -1; $(pid).hidden = !on; }); }

  /* downloads */
  function updateLinks() {
    if (!S.upload || !S.summary) return; const b = "/api/runs/" + S.upload.upload_id + "/download/", q = "?session=" + encodeURIComponent(S.session);
    $("dlPred").href = b + "predictions.csv"; $("dlIv").href = b + "intervals.csv"; $("dlPng").href = b + "chart.png" + q + "&min_run=" + S.minRun; $("dlPdf").href = b + "report.pdf" + q;
  }
  $("delBtn").addEventListener("click", async () => {
    if (!S.upload) return; try { await api("/api/runs/" + S.upload.upload_id, { method: "DELETE" }); } catch (e) { /* already gone */ }
    resetAll(false); setRun("gray", "Waiting for a file"); window.scrollTo({ top: 0 });
  });

  /* ------------------------------------------------------------------ start */
  fetch("/api/health").then((r) => r.json()).then((j) => { $("ver").textContent = j.version; }).catch(() => {});
  loadModel();
  window.__spm = S;   // read-only handle for automated UI tests
})();
