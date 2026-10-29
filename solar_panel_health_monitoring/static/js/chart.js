/* Voltage time-series chart drawn on <canvas>. No external libraries.
   Data comes from /api/runs/<id>/series. Flagged bands come from the model's intervals, never from the drawing code. */
(function (global) {
  "use strict";
  const COLORS = ["#39D5E8", "#7AA7FF", "#C79BFF", "#FF8FD0", "#FFB27A", "#A3B7CF", "#8EE3C8", "#E6E0A0"];
  const RED = "239,82,97";
  const C = { bg: "#0D1728", grid: "#1B2C48", axis: "#91A3BC", text: "#F3F7FF" };

  function lineColor(i) { return COLORS[i % COLORS.length]; }

  function fmtElapsed(t, span, withDay) {
    const neg = t < 0; t = Math.abs(Math.round(t));
    const d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    const p = (n) => String(n).padStart(2, "0");
    let out;
    const day = withDay || span >= 2 * 86400 || t >= 86400;
    if (span >= 2 * 86400) out = d + "d " + p(h) + ":" + p(m);
    else if (day) out = d + "d " + p(h) + ":" + p(m) + (span < 3 * 3600 ? ":" + p(s) : "");
    else if (span >= 3 * 3600) out = p(h) + ":" + p(m);
    else out = p(h) + ":" + p(m) + ":" + p(s);
    return (neg ? "-" : "") + out;
  }
  const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 432000, 864000, 2592000];
  function timeTicks(a, b, n) {
    const raw = (b - a) / n; let step = STEPS.find((s) => s >= raw) || STEPS[STEPS.length - 1];
    const out = []; for (let t = Math.ceil(a / step) * step; t <= b; t += step) out.push(t); return out;
  }
  function niceTicks(a, b, n) {
    const span = b - a || 1, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / mag, step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    const out = []; for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(+v.toFixed(10)); return out;
  }
  function lowerBound(arr, x) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; } return lo; }

  class VoltageChart {
    constructor(opts) {
      this.cv = opts.canvas; this.lane = opts.lane; this.tip = opts.tip; this.msg = opts.msg; this.box = opts.box;
      this.status = opts.status; this.onView = opts.onView || function () {};
      this.ctx = this.cv.getContext("2d"); this.lctx = this.lane.getContext("2d");
      this.series = null; this.hidden = new Set(); this.minRun = 1; this.view = null; this.full = null;
      this.cursor = null; this.drag = null; this.m = { l: 58, r: 14, t: 10, b: 0 };
      this.H = 360; this.rowH = 20; this.laneAxis = 44;
      this._bind();
      this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(this.box);
    }
    destroy() { this.ro.disconnect(); }

    setData(series) {
      this.series = series; this.cursor = null;
      const t = series.t; this.n = t.length;
      if (!this.n) { this.full = [0, 1]; } else {
        const pad = Math.max(series.dt_s || 1, (t[this.n - 1] - t[0]) * 0.004); this.full = [t[0] - pad, t[this.n - 1] + pad];
      }
      this.view = this.full.slice();
      this.names = series.panels.map((p) => p.panel);
      this.draw(); this.onView(this.isZoomed());
    }
    setHidden(set) { this.hidden = set; this.draw(); }
    setMinRun(n) { this.minRun = n; this.draw(); }
    isZoomed() { return this.view && (this.view[0] > this.full[0] + 1e-6 || this.view[1] < this.full[1] - 1e-6); }
    reset() { this.view = this.full.slice(); this.draw(); this.onView(false); }
    zoomTo(a, b) {
      const min = (this.series.dt_s || 1) * 6; if (b - a < min) { const c = (a + b) / 2; a = c - min / 2; b = c + min / 2; }
      a = Math.max(a, this.full[0]); b = Math.min(b, this.full[1]); this.view = [a, b]; this.draw(); this.onView(this.isZoomed());
    }
    zoomFactor(f) { const c = (this.view[0] + this.view[1]) / 2, h = (this.view[1] - this.view[0]) / 2 * f; this.zoomTo(c - h, c + h); }
    focusInterval(t0, t1) { const pad = Math.max((t1 - t0) * 1.5, (this.series.dt_s || 1) * 30); this.zoomTo(t0 - pad, t1 + pad); }

    /* ------------------------------------------------------------------ layout */
    _size() {
      const w = Math.max(280, this.box.clientWidth), dpr = window.devicePixelRatio || 1;
      this.W = w; this.H = w < 560 ? 280 : w < 900 ? 330 : 380;
      this.LH = this.series ? this.series.panels.length * this.rowH + this.laneAxis + 6 : 60;
      for (const [c, h] of [[this.cv, this.H], [this.lane, this.LH]]) {
        c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); c.style.height = h + "px";
      }
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); this.lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.pw = w - this.m.l - this.m.r; this.ph = this.H - this.m.t - this.m.b;
    }
    X(t) { return this.m.l + (t - this.view[0]) / (this.view[1] - this.view[0]) * this.pw; }
    T(x) { return this.view[0] + (x - this.m.l) / this.pw * (this.view[1] - this.view[0]); }

    _yRange() {
      const s = this.series, t = s.t; let lo = Infinity, hi = -Infinity;
      const i0 = Math.max(0, lowerBound(t, this.view[0]) - 1), i1 = Math.min(t.length - 1, lowerBound(t, this.view[1]));
      s.panels.forEach((p) => { if (this.hidden.has(p.panel)) return; for (let i = i0; i <= i1; i++) { const v = p.v[i]; if (v != null) { if (v < lo) lo = v; if (v > hi) hi = v; } } });
      if (!isFinite(lo)) { lo = 0; hi = 6; }
      const pad = Math.max((hi - lo) * 0.07, 0.05); return [lo - pad, hi + pad];
    }
    Y(v) { return this.m.t + (1 - (v - this.y0) / (this.y1 - this.y0)) * this.ph; }

    /* ------------------------------------------------------------------ drawing */
    draw() {
      if (!this.series) return;
      this._size(); const s = this.series, ctx = this.ctx;
      [this.y0, this.y1] = this._yRange();
      ctx.clearRect(0, 0, this.W, this.H); ctx.fillStyle = C.bg; ctx.fillRect(0, 0, this.W, this.H);
      ctx.font = "12px 'Segoe UI',system-ui,sans-serif"; ctx.textBaseline = "middle";
      // grid + y labels
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.fillStyle = C.axis; ctx.textAlign = "right";
      for (const v of niceTicks(this.y0, this.y1, 6)) { const y = Math.round(this.Y(v)) + 0.5; ctx.beginPath(); ctx.moveTo(this.m.l, y); ctx.lineTo(this.W - this.m.r, y); ctx.stroke(); ctx.fillText(v.toFixed(v % 1 ? 1 : 0), this.m.l - 8, y); }
      const span = this.view[1] - this.view[0], ticks = timeTicks(this.view[0], this.view[1], Math.max(3, Math.floor(this.pw / 110)));
      for (const t of ticks) { const x = Math.round(this.X(t)) + 0.5; ctx.beginPath(); ctx.moveTo(x, this.m.t); ctx.lineTo(x, this.H); ctx.stroke(); }
      ctx.save(); ctx.translate(14, this.m.t + this.ph / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = "center"; ctx.fillStyle = C.axis; ctx.fillText("Voltage (V)", 0, 0); ctx.restore();
      ctx.save(); ctx.beginPath(); ctx.rect(this.m.l, this.m.t, this.pw, this.ph); ctx.clip();
      // bands (flagged intervals of visible panels)
      const half = (s.dt_s || 1) / 2, shown = this.shownIntervals();
      for (const iv of shown) {
        if (iv.t1 + half < this.view[0] || iv.t0 - half > this.view[1]) continue;
        let x0 = this.X(iv.t0 - half), x1 = this.X(iv.t1 + half); if (x1 - x0 < 1.6) { const c = (x0 + x1) / 2; x0 = c - 0.8; x1 = c + 0.8; }
        ctx.fillStyle = "rgba(" + RED + "," + (iv.n > 2 ? 0.2 : 0.14) + ")"; ctx.fillRect(x0, this.m.t, x1 - x0, this.ph);
      }
      // lines
      s.panels.forEach((p, k) => { if (!this.hidden.has(p.panel)) this._line(p, k); });
      // flagged markers when few are in view
      this._markers();
      ctx.restore();
      // cursor
      if (this.cursor != null) { const x = Math.round(this.X(s.t[this.cursor])) + 0.5; if (x >= this.m.l && x <= this.W - this.m.r) { ctx.strokeStyle = "rgba(243,247,255,.55)"; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(x, this.m.t); ctx.lineTo(x, this.H); ctx.stroke(); ctx.setLineDash([]); } }
      if (this.drag && this.drag.active) { const a = Math.min(this.drag.x0, this.drag.x1), b = Math.max(this.drag.x0, this.drag.x1); ctx.fillStyle = "rgba(57,213,232,.14)"; ctx.strokeStyle = "rgba(57,213,232,.8)"; ctx.fillRect(a, this.m.t, b - a, this.ph); ctx.strokeRect(a + .5, this.m.t + .5, b - a, this.ph - 1); }
      this._drawLane(ticks, span, shown);
      this._chartMsg();
    }

    shownIntervals() { return this.series.intervals.filter((iv) => iv.n >= this.minRun && !this.hidden.has(iv.panel)); }

    _line(p, k) {
      const ctx = this.ctx, t = this.series.t, v = p.v, gap = this.series.gap_s || 0;
      const i0 = Math.max(0, lowerBound(t, this.view[0]) - 1), i1 = Math.min(t.length - 1, lowerBound(t, this.view[1]));
      ctx.strokeStyle = lineColor(k); ctx.lineWidth = 1.25; ctx.globalAlpha = 0.92; ctx.lineJoin = "round";
      if (k >= COLORS.length) ctx.setLineDash([6, 3]); else ctx.setLineDash([]);
      ctx.beginPath(); let pen = false, lastT = null;
      const move = (x, y, tt) => { if (!pen || (gap && tt - lastT > gap)) ctx.moveTo(x, y); else ctx.lineTo(x, y); pen = true; lastT = tt; };
      const n = i1 - i0 + 1;
      if (n <= this.pw * 2) {
        for (let i = i0; i <= i1; i++) { if (v[i] == null) { pen = false; continue; } move(this.X(t[i]), this.Y(v[i]), t[i]); }
      } else {                                   // min/max per pixel column: spikes and dropouts survive
        let px = null, mn = 0, mx = 0, imn = 0, imx = 0, has = false, tFirst = 0;
        const flush = () => { if (!has) return; const x = this.m.l + px + 0.5; if (imn <= imx) { move(x, this.Y(mn), tFirst); move(x, this.Y(mx), tFirst); } else { move(x, this.Y(mx), tFirst); move(x, this.Y(mn), tFirst); } has = false; };
        for (let i = i0; i <= i1; i++) {
          if (v[i] == null) { flush(); pen = false; px = null; continue; }
          const q = Math.floor(this.X(t[i]) - this.m.l);
          if (q !== px) { flush(); px = q; mn = mx = v[i]; imn = imx = i; tFirst = t[i]; has = true; }
          else { if (v[i] < mn) { mn = v[i]; imn = i; } if (v[i] > mx) { mx = v[i]; imx = i; } }
        }
        flush();
      }
      ctx.stroke(); ctx.globalAlpha = 1; ctx.setLineDash([]);
    }

    _markers() {
      const s = this.series, ctx = this.ctx, t = s.t; const a = lowerBound(t, this.view[0]), b = lowerBound(t, this.view[1]);
      let count = 0; const pick = [];
      s.panels.forEach((p, k) => { if (this.hidden.has(p.panel)) return; for (const i of p.f) { if (i >= a && i <= b) { count++; pick.push([k, i]); } } });
      if (count > 700) return;
      ctx.lineWidth = 1.5; ctx.strokeStyle = "rgb(" + RED + ")"; ctx.fillStyle = "rgba(8,15,29,.9)";
      for (const [k, i] of pick) { const v = s.panels[k].v[i]; if (v == null) continue; ctx.beginPath(); ctx.arc(this.X(t[i]), this.Y(v), 3.2, 0, 7); ctx.fill(); ctx.stroke(); }
    }

    _drawLane(ticks, span, shown) {
      const c = this.lctx, s = this.series, half = (s.dt_s || 1) / 2;
      c.clearRect(0, 0, this.W, this.LH); c.fillStyle = C.bg; c.fillRect(0, 0, this.W, this.LH);
      c.font = "12px 'Segoe UI',system-ui,sans-serif"; c.textBaseline = "middle";
      const rows = s.panels.length, h = rows * this.rowH;
      c.strokeStyle = C.grid; c.lineWidth = 1;
      for (const t of ticks) { const x = Math.round(this.X(t)) + 0.5; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h + 4); c.stroke(); }
      c.beginPath(); c.moveTo(this.m.l, 0.5); c.lineTo(this.W - this.m.r, 0.5); c.stroke();
      s.panels.forEach((p, k) => {
        const y = k * this.rowH, hid = this.hidden.has(p.panel);
        c.fillStyle = hid ? "#5B6E8A" : C.text; c.textAlign = "right"; c.fillText(p.panel.length > 8 ? p.panel.slice(0, 7) + "…" : p.panel, this.m.l - 8, y + this.rowH / 2);
        c.fillStyle = "rgba(145,163,188,.07)"; c.fillRect(this.m.l, y + 4, this.pw, this.rowH - 8);
      });
      const idx = new Map(s.panels.map((p, k) => [p.panel, k]));
      for (const iv of shown) {
        if (iv.t1 + half < this.view[0] || iv.t0 - half > this.view[1]) continue;
        const k = idx.get(iv.panel); let x0 = this.X(iv.t0 - half), x1 = this.X(iv.t1 + half); if (x1 - x0 < 2) { const m = (x0 + x1) / 2; x0 = m - 1; x1 = m + 1; }
        c.fillStyle = "rgb(" + RED + ")"; c.fillRect(x0, k * this.rowH + 4, x1 - x0, this.rowH - 8);
      }
      c.fillStyle = C.axis; c.textAlign = "center";
      for (const t of ticks) { const x = this.X(t); if (x > this.m.l + 14 && x < this.W - this.m.r - 14) c.fillText(fmtElapsed(t, span, this.view[1] >= 86400), x, h + 16); }
      c.textAlign = "center"; c.fillText("Elapsed time" + (span >= 2 * 86400 || this.view[1] >= 86400 ? " (days hh:mm" + (span < 3 * 3600 && span < 2 * 86400 ? ":ss)" : ")") : span >= 3 * 3600 ? " (hh:mm)" : " (hh:mm:ss)"), this.m.l + this.pw / 2, h + 34);
      c.textAlign = "left"; 
    }

    _chartMsg() {
      const s = this.series, vis = s.panels.filter((p) => !this.hidden.has(p.panel));
      if (!s.t.length) { this._showMsg("No readings with a usable timestamp can be drawn for this session."); return; }
      if (!vis.length) { this._showMsg("All panels are hidden. Use the legend to show a panel."); return; }
      this.msg.hidden = true;
    }
    _showMsg(t) { this.msg.textContent = t; this.msg.hidden = false; }

    /* ------------------------------------------------------------------ interaction */
    _bind() {
      const cv = this.cv;
      const pos = (e) => { const r = cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
      cv.addEventListener("pointermove", (e) => {
        const p = pos(e);
        if (this.drag && this.drag.active) { this.drag.x1 = Math.min(Math.max(p.x, this.m.l), this.W - this.m.r); this.draw(); return; }
        this._hover(p.x, p.y);
      });
      cv.addEventListener("pointerleave", () => { if (!this.drag || !this.drag.active) { this.cursor = null; this.tip.hidden = true; this.draw(); } });
      cv.addEventListener("pointerdown", (e) => {
        if (e.pointerType === "touch" || e.button !== 0 || !this.series) return;
        const p = pos(e); if (p.x < this.m.l || p.x > this.W - this.m.r) return;
        this.drag = { active: true, x0: p.x, x1: p.x }; cv.setPointerCapture(e.pointerId); this.tip.hidden = true;
      });
      const end = (e) => {
        if (!this.drag || !this.drag.active) return; const d = this.drag; this.drag = null;
        if (Math.abs(d.x1 - d.x0) > 6) this.zoomTo(this.T(Math.min(d.x0, d.x1)), this.T(Math.max(d.x0, d.x1))); else this.draw();
      };
      cv.addEventListener("pointerup", end); cv.addEventListener("pointercancel", end);
      cv.addEventListener("dblclick", () => this.reset());
      cv.addEventListener("keydown", (e) => {
        if (!this.series || !this.series.t.length) return; const k = e.key;
        if (k === "ArrowLeft" || k === "ArrowRight") {
          e.preventDefault(); const step = e.shiftKey ? 20 : 1; let i = this.cursor == null ? lowerBound(this.series.t, (this.view[0] + this.view[1]) / 2) : this.cursor;
          i = Math.min(this.n - 1, Math.max(0, i + (k === "ArrowRight" ? step : -step))); this.cursor = i;
          if (this.series.t[i] < this.view[0] || this.series.t[i] > this.view[1]) { const w = this.view[1] - this.view[0]; this.zoomTo(this.series.t[i] - w / 2, this.series.t[i] + w / 2); }
          this.draw(); this._tipAt(i, this.X(this.series.t[i]), this.m.t + 20); this._announce(i);
        } else if (k === "+" || k === "=") { e.preventDefault(); this.zoomFactor(0.5); }
        else if (k === "-" || k === "_") { e.preventDefault(); this.zoomFactor(2); }
        else if (k === "Home" || k === "0") { e.preventDefault(); this.reset(); }
      });
      this.lane.addEventListener("pointermove", (e) => {
        const r = this.lane.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, k = Math.floor(y / this.rowH);
        if (!this.series || k < 0 || k >= this.series.panels.length || x < this.m.l) { this.tip.hidden = true; return; }
        const t = this.T(x), name = this.series.panels[k].panel, half = (this.series.dt_s || 1) / 2;
        const iv = this.shownIntervals().find((v) => v.panel === name && t >= v.t0 - half - 1e-9 && t <= v.t1 + half + 1e-9);
        if (!iv) { this.tip.hidden = true; return; }
        this._tipHTML(this.box.clientWidth, this.H + y, [["div", "t", "Panel " + name + " flagged"], ["div", "r", "From", fmtElapsed(iv.t0, 1e9)], ["div", "r", "To", fmtElapsed(iv.t1, 1e9)],
          ["div", "r", "Flagged readings", String(iv.n)], ["div", "r", "Highest score", iv.max_score.toFixed(4)]], x);
      });
      this.lane.addEventListener("pointerleave", () => { this.tip.hidden = true; });
    }
    _announce(i) {
      const s = this.series; const parts = s.panels.filter((p) => !this.hidden.has(p.panel)).map((p) => p.panel + " " + (p.v[i] == null ? "no reading" : p.v[i].toFixed(3) + " volts") + (p.f.includes(i) ? ", flagged" : ""));
      if (this.status) this.status.textContent = "Time " + fmtElapsed(s.t[i], 1e9) + ". " + parts.join("; ");
    }
    _hover(x, y) {
      const s = this.series; if (!s || !s.t.length || x < this.m.l || x > this.W - this.m.r) { this.cursor = null; this.tip.hidden = true; this.draw(); return; }
      const tt = this.T(x); let i = lowerBound(s.t, tt); if (i > 0 && (i === s.t.length || tt - s.t[i - 1] < s.t[i] - tt)) i--;
      this.cursor = i; this.draw(); this._tipAt(i, x, y);
    }
    _tipAt(i, x, y) {
      const s = this.series, rows = [["div", "t", "Elapsed " + fmtElapsed(s.t[i], 1e9)]];
      s.panels.forEach((p, k) => {
        if (this.hidden.has(p.panel)) return; const fl = p.f.length ? bsearchHas(p.f, i) : false;
        rows.push(["div", "r", p.panel, p.v[i] == null ? "no reading" : p.v[i].toFixed(3) + " V", lineColor(k), fl, p.s[i]]);
      });
      this._tipHTML(x, y, rows, x);
    }
    _tipHTML(x, y, rows) {
      const tip = this.tip; tip.textContent = "";
      for (const r of rows) {
        const d = document.createElement("div"); d.className = r[1];
        if (r[1] === "t") { d.textContent = r[2]; } else {
          const l = document.createElement("span"); if (r[4]) { const sw = document.createElement("i"); sw.style.background = r[4]; l.appendChild(sw); } l.appendChild(document.createTextNode(r[2])); d.appendChild(l);
          const b = document.createElement("b"); b.textContent = r[3]; d.appendChild(b);
          if (r[5] !== undefined && r[5]) { const f = document.createElement("span"); f.className = "fl"; f.textContent = "Flagged, score " + (r[6] == null ? "n/a" : r[6].toFixed(4)); d.appendChild(f); d.style.flexWrap = "wrap"; }
        }
        tip.appendChild(d);
      }
      tip.hidden = false; const w = tip.offsetWidth, h = tip.offsetHeight; let left = x + 14; if (left + w > this.box.clientWidth - 4) left = x - w - 14; tip.style.left = Math.max(4, left) + "px"; tip.style.top = Math.max(4, Math.min(y - h / 2, this.box.clientHeight - h - 4)) + "px";
    }
  }
  function bsearchHas(arr, v) { let lo = 0, hi = arr.length - 1; while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m] === v) return true; if (arr[m] < v) lo = m + 1; else hi = m - 1; } return false; }

  global.VoltageChart = VoltageChart; global.SPM_COLORS = { lineColor, fmtElapsed };
})(window);
