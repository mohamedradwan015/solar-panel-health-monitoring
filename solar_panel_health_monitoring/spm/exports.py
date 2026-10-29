"""Download artefacts. All content is taken from stored model outputs; nothing is recomputed with different logic."""
from __future__ import annotations
import io, datetime as dt
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator
from matplotlib.patches import Patch
from . import __version__

BG, PANEL, GRID, TEXT, MUTED = "#080F1D", "#0D1728", "#1D2D47", "#F3F7FF", "#91A3BC"
RED, BLUE, CYAN = "#EF5261", "#2F8CFF", "#39D5E8"
LINE_COLORS = ["#39D5E8", "#7AA7FF", "#C79BFF", "#FF8FD0", "#FFB27A", "#A3B7CF", "#8EE3C8", "#E6E0A0"]
CHART_TITLE = "Panel Voltages (Red Band = Rows Flagged on the Faulty Panel)"
INTERPRETATION = ("Red bands mark readings the model flagged as statistically unusual compared with normal reference data. "
                  "They are not confirmed physical damage.")


def fmt_elapsed(t: float, span: float, with_day: bool = False) -> str:
    t = float(t); neg = t < 0; t = abs(t)
    d, rem = divmod(int(round(t)), 86400); h, rem = divmod(rem, 3600); m, s = divmod(rem, 60)
    day = with_day or span >= 2 * 86400 or t >= 86400
    if span >= 2 * 86400:
        out = f"{d}d {h:02d}:{m:02d}"
    elif day:
        out = f"{d}d {h:02d}:{m:02d}" + (f":{s:02d}" if span < 3 * 3600 else "")
    elif span >= 3 * 3600:
        out = f"{h:02d}:{m:02d}"
    else:
        out = f"{h:02d}:{m:02d}:{s:02d}"
    return ("-" if neg else "") + out


def _arr(x):
    return np.array([np.nan if v is None else v for v in x], float)


def _with_gaps(t: np.ndarray, v: np.ndarray, gap: float):
    if len(t) < 2 or gap <= 0:
        return t, v
    brk = np.where(np.diff(t) > gap)[0]
    if len(brk) == 0:
        return t, v
    return np.insert(t, brk + 1, np.nan), np.insert(v, brk + 1, np.nan)


def render_chart_png(series: dict, summary_line: str | None = None, width_px: int = 1800, height_px: int = 1000, min_run: int = 1) -> bytes:
    t = _arr(series["t"]); gap = series.get("gap_s") or 0.0; dt_s = series.get("dt_s") or 1.0
    panels = series["panels"]; names = [p["panel"] for p in panels]
    span = float(np.nanmax(t) - np.nanmin(t)) if len(t) else 1.0
    dpi = 150
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor=BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[5.2, max(1.0, 0.55 * len(panels))], hspace=0.06, left=0.075, right=0.985, top=0.835, bottom=0.095)
    ax = fig.add_subplot(gs[0]); lane = fig.add_subplot(gs[1], sharex=ax)
    for a in (ax, lane):
        a.set_facecolor(PANEL)
        for sp in a.spines.values():
            sp.set_color(GRID)
        a.tick_params(colors=MUTED, labelsize=8.5, length=3, color=GRID)
    ax.grid(True, color=GRID, lw=0.6, alpha=0.9); lane.grid(True, axis="x", color=GRID, lw=0.6, alpha=0.9)

    by_panel: dict[str, list] = {}
    all_iv = series["intervals"]; shown_iv = [iv for iv in all_iv if iv["n"] >= min_run]
    for iv in shown_iv:
        by_panel.setdefault(iv["panel"], []).append(iv)
    min_w = max(span * 0.0012, dt_s)
    ymins, ymaxs = [], []
    for k, p in enumerate(panels):
        v = _arr(p["v"]); tt, vv = _with_gaps(t, v, gap)
        ax.plot(tt, vv, color=LINE_COLORS[k % len(LINE_COLORS)], lw=0.85, alpha=0.88, ls="-" if k < len(LINE_COLORS) else "--", label=p["panel"], zorder=3)
        if np.isfinite(v).any():
            ymins.append(np.nanmin(v)); ymaxs.append(np.nanmax(v))
    lo, hi = (min(ymins), max(ymaxs)) if ymins else (0.0, 1.0)
    pad = max((hi - lo) * 0.06, 0.05); y0, y1 = lo - pad, hi + pad
    ax.set_ylim(y0, y1)
    for k, name in enumerate(names):
        rng = [(iv["t0"] - max(dt_s, min_w) / 2, max(iv["t1"] - iv["t0"] + dt_s, min_w)) for iv in by_panel.get(name, [])]
        if rng:
            ax.broken_barh(rng, (y0, y1 - y0), facecolors=RED, alpha=0.15, lw=0, zorder=1)
            lane.broken_barh(rng, (k - 0.34, 0.68), facecolors=RED, alpha=0.95, lw=0, zorder=2)
    lane.set_ylim(len(names) - 0.4, -0.6); lane.set_yticks(range(len(names))); lane.set_yticklabels(names, color=TEXT, fontsize=8.5)
    lane.set_xlabel("Elapsed time" + (" (days hh:mm)" if (span >= 2 * 86400 or np.nanmax(t) >= 86400) else " (hh:mm)" if span >= 3 * 3600 else " (hh:mm:ss)"), color=MUTED, fontsize=9)
    ax.set_ylabel("Voltage (V)", color=MUTED, fontsize=9)
    plt.setp(ax.get_xticklabels(), visible=False)
    lane.xaxis.set_major_locator(MaxNLocator(8)); lane.xaxis.set_major_formatter(FuncFormatter(lambda x, _: fmt_elapsed(x, span, bool(np.nanmax(t) >= 86400))))
    if len(t) and np.isfinite(t).any():
        pad_x = max(dt_s, span * 0.005); ax.set_xlim(np.nanmin(t) - pad_x, np.nanmax(t) + pad_x)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor=RED, alpha=0.6)); labels.append("Flagged by model")
    leg = ax.legend(handles, labels, loc="lower right", ncol=min(len(labels), 6), fontsize=8.5, frameon=True, facecolor=BG, edgecolor=GRID, labelcolor=TEXT)
    leg.set_zorder(10)
    fig.text(0.075, 0.935, CHART_TITLE, color=TEXT, fontsize=16, fontweight="bold", ha="left", va="center")
    sub = summary_line or ""
    fig.text(0.075, 0.895, sub, color=MUTED, fontsize=9, ha="left", va="center")
    note = INTERPRETATION
    if min_run > 1:
        note += f" Display filter: only runs of {min_run}+ consecutive flagged readings are drawn ({len(shown_iv):,} of {len(all_iv):,} intervals); the CSV holds every flag."
    fig.text(0.075, 0.862, note, color=MUTED, fontsize=8.5, ha="left", va="center")
    if series.get("iso_start"):
        fig.text(0.985, 0.025, f"First sample: {series['iso_start']} UTC", color=MUTED, fontsize=8, ha="right")
    fig.text(0.075, 0.025, f"Solar Panel Health Monitoring v{__version__}", color=MUTED, fontsize=8, ha="left")
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=BG); plt.close(fig)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------- PDF report
def render_report_pdf(summary: dict, model_info: dict, intervals_df, chart_png: bytes | None, input_meta: dict, warnings: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether

    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=19, leading=23, alignment=0, textColor=colors.HexColor("#0B1B33"))
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=12.5, leading=15, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#12315C"))
    B = ParagraphStyle("B", parent=ss["BodyText"], fontSize=9, leading=12.2)
    SM = ParagraphStyle("SM", parent=B, fontSize=8, leading=10.5, textColor=colors.HexColor("#4A5B73"))

    def table(rows, widths, head=True, zebra=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
        st = [("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C5D0DF")),
              ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        if head:
            st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12315C")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        if zebra:
            st += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FB")])]
        t.setStyle(TableStyle(st)); return t

    ov = summary["overview"]; story = []
    story += [Paragraph("Solar Panel Health Monitoring - analysis report", H1),
              Paragraph("Satellite telemetry analysis and anomaly detection", SM), Spacer(1, 4)]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(table([["Input file", input_meta.get("name", "")], ["File size", input_meta.get("size_text", "")], ["Analysis date", now],
                        ["Satellite label", summary.get("satellite_label", "")], ["Sessions analysed", str(ov["sessions"])]], [40 * mm, 130 * mm], head=False, zebra=False))
    story.append(Paragraph("Model outputs (taken as returned by the supplied model)", H2))
    story.append(Paragraph(f"Model: {model_info['name']} (package {model_info['package_version']}), feature set <b>{model_info['feature_set']}</b> "
                           f"({', '.join(model_info['features'])}), k = {model_info['k']}, scaler = {model_info['scaler']}. "
                           f"Per-reading score = mean distance to the {model_info['k']} nearest normal reference readings in scaled (V, I) space. "
                           f"A reading is flagged when score &gt; <b>{model_info['threshold']:.6f}</b>, the {model_info['threshold_percentile']}th percentile of "
                           f"out-of-session reference scores ({model_info['reference_rows']:,} reference readings from {model_info['reference_sessions']} sessions). "
                           "The score is a distance. It is not a probability and not a damage percentage.", B))
    story.append(Paragraph("Derived by this application (simple arithmetic on the model outputs)", H2))
    story.append(table([["Telemetry records (timestamps)", f"{ov['observations']:,}"], ["Panels analysed", f"{ov['panels']}  ({', '.join(ov['panel_names'])})"],
                        ["Panel readings scored", f"{ov['scored_readings']:,}"], ["Unscorable readings (missing voltage)", f"{ov['unscorable_readings']:,}"],
                        ["Flagged readings", f"{ov['flagged_readings']:,}"], ["Flagged share of scored readings", f"{ov['flagged_pct_of_scored']:.3f} %" if ov['flagged_pct_of_scored'] is not None else "n/a"],
                        ["Flagged intervals (consecutive flagged readings)", f"{ov['intervals']:,}"]], [75 * mm, 95 * mm], head=False))
    story.append(Paragraph("The model is designed so that about 0.5 % of healthy readings exceed its threshold; held-out healthy data measured 0.3-0.6 %. "
                           "A flagged share near that level is therefore expected on healthy panels.", SM))
    for s in summary["sessions"]:
        rows = [["Panel", "Readings", "Scored", "Flagged", "Flagged %", "Intervals", "Median V", "Max score", "App triage*"]]
        for p in s["panels"]:
            rows.append([p["panel"], f"{p['samples']:,}", f"{p['scored']:,}", f"{p['flagged']:,}", "n/a" if p["flagged_pct"] is None else f"{p['flagged_pct']:.2f}",
                         f"{p['intervals']:,}", "n/a" if p["median_v"] is None else f"{p['median_v']:.3f}", "n/a" if p["max_score"] is None else f"{p['max_score']:.4f}", p["triage"]["label"]])
        story.append(KeepTogether([Paragraph(f"Panel results - session {s['name']}", H2), table(rows, [18 * mm, 20 * mm, 20 * mm, 18 * mm, 19 * mm, 18 * mm, 19 * mm, 19 * mm, 38 * mm])]))
    tr = summary["triage_rules"]
    story.append(Paragraph(f"*App triage is a convenience label added by this application, not a model output or a verified fault class: "
                           f"'Within expected alert rate' = at most {tr['within_expected_max_pct']:g} % flagged and no persistent run; 'High alert rate' = at least "
                           f"{tr['high_min_pct']:g} % flagged; otherwise 'Review'; 'Insufficient data' = fewer than {tr['min_scored']} scored readings.", SM))
    if intervals_df is not None and len(intervals_df):
        top = intervals_df.sort_values(["flagged_readings", "max_score"], ascending=False).head(15)
        rows = [["Session", "Panel", "Start (s)", "End (s)", "Readings", "Max score", "Dominant diagnosis"]]
        for _, r in top.iterrows():
            rows.append([str(r["session"])[:18], r["panel"], f"{r['start_t_s']:.0f}", f"{r['end_t_s']:.0f}", f"{int(r['flagged_readings'])}", f"{r['max_score']:.4f}", r["dominant_diagnosis"]])
        story.append(KeepTogether([Paragraph("Longest flagged intervals (top 15 of %d)" % len(intervals_df), H2), table(rows, [30 * mm, 18 * mm, 22 * mm, 22 * mm, 18 * mm, 22 * mm, 42 * mm])]))
    if chart_png:
        story += [PageBreak(), Paragraph("Panel voltages with flagged intervals", H2), Image(io.BytesIO(chart_png), width=180 * mm, height=180 * mm * 1000 / 1800)]
    story.append(Paragraph("Data-quality notes", H2))
    story.append(Paragraph("<br/>".join(warnings) if warnings else "No data-quality warnings were raised for the analysed sheets.", B))
    story.append(Paragraph("How to read this report", H2))
    story.append(Paragraph("The model screens each panel reading for statistical unusualness. It does not confirm physical faults, locate failed cells or predict failures. "
                           "Evidence for its detection performance is synthetic or rule-derived; no independently verified real fault labels were available, "
                           "and subtle faults (partial degradation, stuck cells, intermittent bursts) are mostly missed. The baseline is calibrated for BIRDS-bus-like "
                           "voltage and current scales; other satellites may need a new baseline. See docs/release_docs/FINAL_MODEL_CARD.md.", B))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title="Solar Panel Health Monitoring report", author="Solar Panel Health Monitoring").build(story)
    return buf.getvalue()
