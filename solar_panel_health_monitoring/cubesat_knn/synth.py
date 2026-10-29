"""Synthetic fault injection (dev benchmarks) and the sealed 7-day holdout generator. Everything here is SYNTHETIC."""
from __future__ import annotations
import numpy as np, pandas as pd

FAULT_TYPES = ["partial_degradation", "complete_zero_output", "stuck_cell_like", "sudden_shift", "intermittent_bursts", "noise_corruption"]
SEV_RANGE = {"partial_degradation": (.3, .6), "complete_zero_output": (1, 1), "stuck_cell_like": (1, 1), "sudden_shift": (.5, .7),
             "intermittent_bursts": (.4, .6), "noise_corruption": (.2, .4)}


def _apply_fault(V, I, rs, ty, sev, lit):
    """Mutates V, I (1-D arrays for ONE panel/segment). lit = boolean mask of illuminated samples. Returns boolean mask of modified samples."""
    act = np.zeros(len(V), bool)
    if ty == "partial_degradation": I[lit] *= (1 - sev); V[lit] *= (1 - sev / 8); act = lit.copy()
    elif ty == "complete_zero_output": V[:] = np.abs(rs.normal(1.5, .5, len(V))); I[:] = 0.0; act[:] = True
    elif ty == "stuck_cell_like": k = lit.sum(); V[lit] = rs.uniform(2200, 2800, k); I[lit] = rs.uniform(0, 5, k); act = lit.copy()
    elif ty == "sudden_shift": V[lit] *= (1 - sev); I[lit] *= (1 - sev); act = lit.copy()
    elif ty == "intermittent_bursts":
        n = len(V); blen = max(3, n // 100)
        for s0 in range(0, n, blen * 4):
            m = np.zeros(n, bool); m[s0:s0 + blen] = True; m &= lit; I[m] *= (1 - sev); act |= m
    elif ty == "noise_corruption":
        V[:] = np.clip(V + rs.normal(0, sev * 1500, len(V)), 0, None); I[:] = np.abs(I + rs.normal(0, sev * 150, len(I))); act[:] = True
    else: raise ValueError(ty)
    return act


def make_plan(table: pd.DataFrame, sessions, seed: int, dimming_every: int = 2):
    rs = np.random.RandomState(seed); ch = sorted(c[2:] for c in table.columns if c.startswith("V:")); plan = []
    for i, s in enumerate(sessions):
        ty = FAULT_TYPES[(i + seed) % 6]; c = ch[rs.randint(len(ch))]; a = rs.uniform(.2, .45); lo, hi = SEV_RANGE[ty]
        plan.append(dict(session=s, channel=c, type=ty, start_frac=a, end_frac=a + rs.uniform(.25, .4), severity=float(rs.uniform(lo, hi))))
        if i % 3 == 2:
            c2 = [x for x in ch if x != c][rs.randint(len(ch) - 1)]; ty2 = FAULT_TYPES[(i + seed + 2) % 6]; lo, hi = SEV_RANGE[ty2]
            plan.append(dict(session=s, channel=c2, type=ty2, start_frac=a, end_frac=a + .3, severity=float(rs.uniform(lo, hi))))
        if dimming_every and i % dimming_every == 1:
            plan.append(dict(session=s, channel="*", type="common_mode_dimming", start_frac=.55, end_frac=.7, severity=float(rs.uniform(.4, .6))))
    return plan


def inject(table: pd.DataFrame, plan: list[dict], seed: int):
    """Returns (modified copy, fault truth rows, dimming rows). Input is never modified.
    Faults are applied first; common-mode dimming (a HEALTHY event, all channels) afterwards."""
    rs = np.random.RandomState(seed)
    T = table.sort_values(["session", "t_s", "source_row"], kind="stable").reset_index(drop=True); truth = []; dim = []
    for ev, p in enumerate(plan):
        if p["type"] == "common_mode_dimming": continue
        ix = np.where(T["session"].values == p["session"])[0]; n = len(ix); seg = ix[int(p["start_frac"] * n):int(p["end_frac"] * n)]
        Vc, Ic = f"V:{p['channel']}", f"I:{p['channel']}"; V = T.loc[seg, Vc].to_numpy(float).copy(); I = T.loc[seg, Ic].to_numpy(float).copy()
        lit = V > 3500; act = _apply_fault(V, I, rs, p["type"], p["severity"], lit)
        T.loc[seg, Vc] = V; T.loc[seg, Ic] = I
        for r in seg[act]: truth.append((T.at[r, "satellite"], p["session"], int(T.at[r, "source_row"]), p["channel"], p["type"], p["severity"], ev, float(T.at[r, "t_s"])))
    for ev, p in enumerate(plan):
        if p["type"] != "common_mode_dimming": continue
        ix = np.where(T["session"].values == p["session"])[0]; n = len(ix); seg = ix[int(p["start_frac"] * n):int(p["end_frac"] * n)]
        for c in [c[2:] for c in T.columns if c.startswith("V:")]:
            V = T.loc[seg, f"V:{c}"].to_numpy(float).copy(); I = T.loc[seg, f"I:{c}"].to_numpy(float).copy(); lit = V > 3500
            V[lit] *= (1 - p["severity"] / 10); I[lit] *= (1 - p["severity"]); T.loc[seg, f"V:{c}"] = V; T.loc[seg, f"I:{c}"] = I
        for r in seg: dim.append((T.at[r, "satellite"], p["session"], int(T.at[r, "source_row"])))
    cols = ["satellite", "session", "source_row", "channel", "fault_type", "severity", "event_id", "t_s"]
    return T, pd.DataFrame(truth, columns=cols), pd.DataFrame(dim, columns=["satellite", "session", "source_row"]).drop_duplicates()


def events_from_truth(truth: pd.DataFrame) -> pd.DataFrame:
    return truth.groupby(["event_id", "satellite", "session", "channel", "fault_type"]).agg(t_start=("t_s", "min"), t_end=("t_s", "max"), n_rows=("t_s", "size")).reset_index()


def event_metrics(pred: pd.DataFrame, truth: pd.DataFrame, min_run: int = 3, tol_s: float = 60.0) -> dict:
    """Event matching rule: an event is DETECTED if >=1 alert on its (session, channel) lies inside [t_start, t_end].
    A FALSE EVENT is a run of >=min_run consecutive alerts (time-ordered, per session+channel) with no overlap of
    [t_start-tol_s, t_end+tol_s] of any true event on that channel. Delay = first alert time - t_start."""
    ev = events_from_truth(truth); rows = []
    P = pred.sort_values(["session", "channel", "t_s"])
    for _, e in ev.iterrows():
        g = P[(P.session == e.session) & (P.channel == e.channel) & (P.t_s >= e.t_start) & (P.t_s <= e.t_end) & P.anomaly]
        rows.append(dict(event_id=e.event_id, channel=e.channel, fault_type=e.fault_type, detected=len(g) > 0, delay_s=(g.t_s.min() - e.t_start) if len(g) else np.nan))
    R = pd.DataFrame(rows); fe = 0
    for (ses, ch), g in P.groupby(["session", "channel"]):
        a = g["anomaly"].to_numpy(bool); t = g["t_s"].to_numpy(float); evc = ev[(ev.session == ses) & (ev.channel == ch)]; i = 0
        while i < len(a):
            if a[i]:
                j = i
                while j + 1 < len(a) and a[j + 1]: j += 1
                if j - i + 1 >= min_run and not ((evc.t_start - tol_s <= t[j]) & (evc.t_end + tol_s >= t[i])).any(): fe += 1
                i = j + 1
            else: i += 1
    n_obs = int(pred["score"].notna().sum())
    return dict(n_events=len(R), detected=int(R.detected.sum()), event_recall=float(R.detected.mean()) if len(R) else np.nan, missed=int((~R.detected).sum()),
                false_events=fe, false_events_per_1000_obs=1000 * fe / max(n_obs, 1), median_delay_s=float(R.delay_s.median()) if R.detected.any() else np.nan), R


# ---------------------------------------------------------------- sealed 7-day generator
def _rot(ax, ay, az):
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az); n = len(ax); R = np.zeros((n, 3, 3))
    Rx = np.zeros((n, 3, 3)); Rx[:, 0, 0] = 1; Rx[:, 1, 1] = cx; Rx[:, 1, 2] = -sx; Rx[:, 2, 1] = sx; Rx[:, 2, 2] = cx
    Ry = np.zeros((n, 3, 3)); Ry[:, 1, 1] = 1; Ry[:, 0, 0] = cy; Ry[:, 0, 2] = sy; Ry[:, 2, 0] = -sy; Ry[:, 2, 2] = cy
    Rz = np.zeros((n, 3, 3)); Rz[:, 2, 2] = 1; Rz[:, 0, 0] = cz; Rz[:, 0, 1] = -sz; Rz[:, 1, 0] = sz; Rz[:, 1, 1] = cz
    return np.einsum("nij,njk,nkl->nil", Rz, Ry, Rx)


GEN_DEFAULT = dict(v_dark=1300.0, v_lit=4750.0, v_slope=550.0, i_max=520.0, ecl_max=0.38, sig_w=0.035, p_orb=5556.0)


def generate_healthy(seed, days, dt=10.0, n_panels=4, P=None):
    P = {**GEN_DEFAULT, **(P or {})}; rs = np.random.RandomState(seed); n = int(days * 86400 / dt); t = np.arange(n) * dt
    beta = 60 * np.sin(2 * np.pi * t / (86400 * 9.0) + rs.uniform(0, 6.28)); ecl = np.clip(P["ecl_max"] * (1 - (np.abs(beta) / 72) ** 2), 0, P["ecl_max"])
    in_ecl = ((t % P["p_orb"]) / P["p_orb"]) > (1 - ecl)
    w = np.deg2rad(np.array([3.0, 2.7, 3.2])); ph = rs.uniform(0, 6.28, 3)
    R = _rot(w[0] * t + ph[0], w[1] * t + ph[1], w[2] * t + ph[2]); s0 = np.array([.3, .5, .81]); s0 /= np.linalg.norm(s0)
    s_body = np.einsum("nji,j->ni", R, s0); normals = np.array([[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, 0, 1]])[:n_panels]
    l = np.clip(s_body @ normals.T, 0, None)
    alb = P.get("albedo", 0.03) * np.clip(np.cos(2 * np.pi * t[:, None] / P["p_orb"] + rs.uniform(0, 6.28, n_panels)[None, :]), 0, None)   # Earth-shine term
    l = (l + alb) * (~in_ecl)[:, None]
    return t, l, in_ecl, P, rs


def panel_vi(l, P, rs, gains):
    """Illumination l -> (V mV, I mA). If P has real-data quantile tables ('Vq','Iq'), marginals are quantile-matched to real
    flight telemetry (comonotone in l); otherwise a simple analytic curve is used."""
    if "Vq" in P:
        u = pd.Series(l.ravel()).rank(method="average").to_numpy() / l.size; grid = np.linspace(0, 1, len(P["Vq"]))
        V = np.interp(u, grid, P["Vq"]).reshape(l.shape); I = np.interp(u, grid, P["Iq"]).reshape(l.shape) * gains[None, :]
    else:
        sig = 1 - np.exp(-l / P["sig_w"]); V = P["v_dark"] + (P["v_lit"] - P["v_dark"]) * sig + P["v_slope"] * np.clip(l, 0, 1) * sig
        I = P["i_max"] * np.clip(l - 0.02, 0, None) * gains[None, :]
    V = V + rs.normal(0, 1, V.shape) * np.where(l > 0.02, 12, 4); I = I + rs.normal(0, 2, I.shape) * (l > 0.02)
    return np.round(V / 1.5263) * 1.5263, np.clip(np.round(I / 4.661) * 4.661, 0, None)


def generate_syn7(seed=9001, days=7, dt=10.0, n_panels=4, P=None):
    """Returns telemetry (benchmark-style: volts/amps), truth (long form), events. Faults/dimming scheduled from the seed."""
    t, l, in_ecl, P, rs = generate_healthy(seed, days, dt, n_panels, P); n = len(t); gains = rs.uniform(.93, 1.07, n_panels)
    # common-mode dimming (healthy hard negatives): scales illumination of ALL panels
    dim = np.zeros(n, bool); sched = [(1.5, .5, 2.0), (3.2, .4, 2.0), (5.4, .6, 2.0)]
    for d0, depth, hrs in sched:
        m = (t >= d0 * 86400) & (t < d0 * 86400 + hrs * 3600); l[m] *= (1 - depth); dim |= m
    Vh, Ih = panel_vi(l, P, rs, gains); V, I = Vh.copy(), Ih.copy()
    fault = np.zeros((n, n_panels), bool); ftype = np.full((n, n_panels), "", dtype=object); evid = np.full((n, n_panels), -1)
    types = FAULT_TYPES * 2; dur_h = {"partial_degradation": 6, "complete_zero_output": 3, "stuck_cell_like": 4, "sudden_shift": 5, "intermittent_bursts": 8, "noise_corruption": 2}
    busy = [[] for _ in range(n_panels)]; events = []; k = 0
    for ty in types:
        for _try in range(200):
            p = rs.randint(n_panels); st = (rs.uniform(.3, days - .6) if days >= 1 else rs.uniform(.05 * days, .5 * days)) * 86400; en = st + dur_h[ty] * 3600
            if all(en < a - 3600 or st > b + 3600 for a, b in busy[p]) and sum(1 for q in range(n_panels) if any(not (en < a or st > b) for a, b in busy[q])) < 2:
                busy[p].append((st, en)); break
        m = np.where((t >= st) & (t < en))[0]; sev = float(rs.uniform(*SEV_RANGE[ty])); Vs, Is = V[m, p].copy(), I[m, p].copy(); lit = Vs > 3500
        if len(m) == 0: continue
        act = _apply_fault(Vs, Is, rs, ty, sev, lit); V[m, p], I[m, p] = Vs, Is
        if not act.any(): continue
        fault[m[act], p] = True; ftype[m[act], p] = ty; evid[m[act], p] = k
        events.append(dict(event_id=k, panel=f"P{p+1}", fault_type=ty, severity_param=sev, t_start=float(t[m[act]].min()), t_end=float(t[m[act]].max()), n_rows=int(act.sum()))); k += 1
    miss = (t >= 4 * 86400) & (t < 4 * 86400 + 1800); Vout, Iout = V.copy(), I.copy(); Vout[miss, 2] = np.nan; Iout[miss, 2] = np.nan
    t0 = pd.Timestamp("2026-01-01T00:00:00Z"); tel = pd.DataFrame({"timestamp_utc": (t0 + pd.to_timedelta(t, unit="s")).strftime("%Y-%m-%dT%H:%M:%SZ"), "elapsed_seconds": t})
    for p in range(n_panels): tel[f"P{p+1}_voltage_V"] = Vout[:, p] / 1000.0; tel[f"P{p+1}_current_A"] = Iout[:, p] / 1000.0
    rows = []
    for p in range(n_panels):
        pw_h, pw = Vh[:, p] * Ih[:, p], V[:, p] * I[:, p]; sv = np.where(fault[:, p] & (pw_h > 0), 1 - pw / np.where(pw_h > 0, pw_h, np.nan), np.nan)
        rows.append(pd.DataFrame({"elapsed_seconds": t, "panel_id": f"P{p+1}", "fault_flag": fault[:, p], "fault_type": ftype[:, p], "event_id": evid[:, p],
                                  "common_mode_dimming": dim, "missing_measurement": miss if p == 2 else False, "severity_true": np.clip(sv, 0, 1),
                                  "healthy_voltage_V": Vh[:, p] / 1000.0, "healthy_current_A": Ih[:, p] / 1000.0}))
    return tel, pd.concat(rows, ignore_index=True), pd.DataFrame(events)
