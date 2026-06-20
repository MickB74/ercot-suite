"""Streamlit UI for the ERCOT price forecast — shared by the standalone app and
Data Hub page 16. Call ``render()`` from inside a Streamlit script."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import forecast
import forecast_store
import gas_curve
import pf_history
import pf_paths
import public_forecasts
import shape as shaping


def _fan_chart(curve: pd.DataFrame, block: str, hub: str,
               crosscheck: pd.DataFrame | None = None) -> go.Figure:
    c = curve[curve["block"] == block].copy()
    c["month"] = pd.to_datetime(c["month"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=c["month"], y=c["p90"], line=dict(width=0),
                             name="P90", showlegend=False))
    fig.add_trace(go.Scatter(x=c["month"], y=c["p10"], line=dict(width=0),
                             fill="tonexty", fillcolor="rgba(31,119,180,0.18)",
                             name="P10–P90"))
    fig.add_trace(go.Scatter(x=c["month"], y=c["p50"], line=dict(color="#1f77b4", width=2.5),
                             name="P50"))
    traded = c.dropna(subset=["traded"]) if "traded" in c.columns else c.iloc[0:0]
    if not traded.empty:
        fig.add_trace(go.Scatter(x=traded["month"], y=traded["traded"], mode="markers",
                                 marker=dict(color="#d62728", size=8, symbol="diamond"),
                                 name="Traded futures"))
    if crosscheck is not None and not crosscheck.empty:
        cc = crosscheck[(crosscheck["month"] >= c["month"].min())
                        & (crosscheck["month"] <= c["month"].max())]
        if not cc.empty:
            fig.add_trace(go.Scatter(x=cc["month"], y=cc["price"], mode="lines",
                                     line=dict(color="#7f7f7f", width=1.5, dash="dot"),
                                     name="EIA STEO US retail (cross-check)"))
    fig.update_layout(title=f"{hub} — {block} forward ($/MWh)", height=380,
                      margin=dict(l=10, r=10, t=40, b=10),
                      yaxis_title="$/MWh", hovermode="x unified")
    return fig


def _heat_rate_chart(rt) -> go.Figure:
    import heat_rate

    b = heat_rate.buckets(rt)
    fig = go.Figure()
    colors = {"peak": "#d62728", "offpeak": "#1f77b4"}
    for block, label in (("peak", "Peak"), ("offpeak", "Off-peak")):
        s = b[b["block"] == block].sort_values("month")
        x = [heat_rate.MONTH_NAMES[m - 1] for m in s["month"]]
        c = colors[block]
        rgba = "rgba(214,39,40,0.12)" if block == "peak" else "rgba(31,119,180,0.12)"
        fig.add_trace(go.Scatter(x=x, y=s["ihr_p90"], line=dict(width=0),
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=s["ihr_p10"], line=dict(width=0), fill="tonexty",
                                 fillcolor=rgba, name=f"{label} P10–P90"))
        fig.add_trace(go.Scatter(x=x, y=s["ihr_p50"], line=dict(color=c, width=2.5),
                                 mode="lines+markers", name=f"{label} median"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_title="Implied heat rate (MMBtu/MWh)",
                      hovermode="x unified",
                      title="Implied heat rate by month — median line, P10–P90 band")
    return fig


_BLOCK_LABELS = {"Round-the-clock (ATC)": "atc", "On-peak (5×16)": "peak",
                 "Off-peak": "offpeak"}
_METRIC_LABELS = {"P50 (median / expected)": "p50", "P10 (low case)": "p10",
                  "P90 (high case)": "p90", "Mean": "mean"}


def _heat_color(v, vmin, vmax):
    if pd.isna(v):
        return ""
    t = 0.0 if vmax == vmin else (float(v) - vmin) / (vmax - vmin)
    r = int(255 * min(1.0, 2 * t))          # low=green, mid=yellow, high=red
    g = int(255 * min(1.0, 2 * (1 - t)))
    return f"background-color: rgba({r},{g},80,0.30)"


@st.cache_data(show_spinner=False)
def _rt(hub: str) -> pd.DataFrame:
    """Cached RTM 15-min history for one hub (the parquet is 1.6M rows)."""
    return pf_history.load_rt15(hub)


@st.cache_data(show_spinner=False)
def _actuals(hub: str) -> pd.DataFrame:
    """Realized monthly block-average prices for one hub (year, month, block, price)."""
    return pf_history.monthly_block_mean(_rt(hub))


@st.cache_data(show_spinner=False)
def _backtest(hub: str, horizon: int):
    import backtest
    df = backtest.run_backtest(hub, horizon_months=horizon, n_sims=3000)
    return df, backtest.summarize(df)


def _calibration_view(hub: str, horizon: int) -> None:
    st.markdown("#### Backtest — how accurate is the model, really?")
    st.caption("Walk-forward: for each past month we **re-train heat rates only on "
               "data before it**, forecast forward, and score against what actually "
               "settled. Gas is held at its realized value (we don't have historical "
               "gas *forwards* to replay), so this isolates the heat-rate model and "
               "the scenario bands — the parts we built.")
    if not st.button("Run backtest", key="run_bt", type="primary"):
        st.info("Runs a few seconds — trains and scores hundreds of historical forecasts.")
        return

    with st.spinner(f"Backtesting {hub}…"):
        df, s = _backtest(hub, horizon)
    if df.empty:
        st.warning("Not enough history to backtest this hub.")
        return
    ov = s["overall"]

    c = st.columns(4)
    c[0].metric("P50 bias", f"{ov['bias_%']:+.1f}%",
                help="Average forecast minus realized. Negative = we under-forecast.")
    c[1].metric("MAPE", f"{ov['mape_%']:.0f}%", help="Typical absolute monthly error.")
    c[2].metric("P10–P90 coverage", f"{ov['coverage80']:.0%}",
                f"{(ov['coverage80']-0.80)*100:+.0f} pts vs 80%",
                delta_color="off", help="Share of realized inside the band. Target 80%; "
                "below = bands too narrow, above = too wide.")
    c[3].metric("Realized below P50", f"{ov['pit_below50']:.0%}",
                f"{(ov['pit_below50']-0.50)*100:+.0f} pts vs 50%", delta_color="off",
                help="Target 50%. Below 50% means realized usually beats P50 → median too low.")

    # plain-language verdict
    notes = []
    if abs(ov["bias_%"]) > 5:
        notes.append(f"**P50 is biased {('low' if ov['bias_%']<0 else 'high')} "
                     f"by {abs(ov['bias_%']):.0f}%** — realized prices came in "
                     f"{'above' if ov['bias_%']<0 else 'below'} the central forecast.")
    if ov["coverage80"] < 0.75:
        notes.append(f"**Bands are too narrow** ({ov['coverage80']:.0%} coverage vs 80% "
                     "target) — widen scenario dispersion.")
    elif ov["coverage80"] > 0.85:
        notes.append(f"Bands are a bit **wide** ({ov['coverage80']:.0%} vs 80%).")
    if notes:
        st.warning(" ".join(notes))
    else:
        st.success("Well calibrated: low bias and ~80% coverage.")

    st.markdown("##### By forecast horizon")
    st.dataframe(s["by_horizon"][["n", "bias_%", "mape_%", "coverage80", "pit_below50"]].round(1),
                 use_container_width=True)
    st.markdown("##### By block")
    st.dataframe(s["by_block"][["n", "bias_%", "mape_%", "coverage80"]].round(1),
                 use_container_width=True)
    st.caption("This backtests **price given gas**. Live forecasts also carry gas-"
               "forecast error (gas comes from traded futures, which are themselves "
               "uncertain) — so treat these as the model's *floor* on error.")


def _actual_lookup(hub: str, year: int, moy: int, block: str):
    a = _actuals(hub)
    r = a[(a["year"] == year) & (a["month"] == moy) & (a["block"] == block)]
    return float(r["price"].iloc[0]) if not r.empty else np.nan


def _full_history_years(hub: str) -> list[int]:
    """Calendar years with ~complete (>=11 month) history, newest first."""
    a = _actuals(hub)
    a = a[a["block"] == "atc"]
    counts = a.groupby("year")["month"].nunique()
    return sorted([int(y) for y, c in counts.items() if c >= 11], reverse=True)


def _price_matrix_view(curve: pd.DataFrame, hubs: list[str], asof) -> None:
    st.markdown("#### Hub × month price ($/MWh)")
    c1, c2 = st.columns(2)
    block_lbl = c1.radio("Block", list(_BLOCK_LABELS), horizontal=True,
                         help="Round-the-clock = every hour. On-peak = ERCOT 5×16 "
                              "(weekdays, hours-ending 7–22). Off-peak = nights + weekends.")
    metric_lbl = c2.radio("Scenario", list(_METRIC_LABELS), horizontal=True,
                          help="P50 = central forecast. P10/P90 = the low/high "
                               "scenario band from the Monte Carlo. Mean = average "
                               "across all paths (above P50 when the upside tail is fat).")
    block, metric = _BLOCK_LABELS[block_lbl], _METRIC_LABELS[metric_lbl]

    avail_years = _full_history_years(hubs[0])
    cc1, cc2 = st.columns([1, 2])
    compare = cc1.checkbox("Compare to actual history", value=True,
                           help="Add columns of realized prices for the same calendar "
                                "month in prior years, so you can see the forecast vs "
                                "what that month actually settled.")
    cmp_years = []
    if compare and avail_years:
        default = [avail_years[0]] if avail_years else []
        cmp_years = cc2.multiselect("Comparison year(s)", avail_years, default=default,
                                    help="Which past calendar years to show actuals for.")

    piv = forecast.price_matrix(curve, block=block, metric=metric)
    piv = piv.reindex(columns=[h for h in hubs if h in piv.columns])
    moy = [int(m[5:7]) for m in piv.index]

    if len(hubs) == 1:
        hub = hubs[0]
        df = pd.DataFrame(index=piv.index)
        df.index.name = "Month"
        df[f"Forecast {metric.upper()}"] = piv[hub].values
        for y in cmp_years:
            df[f"{y} actual"] = [_actual_lookup(hub, y, m, block) for m in moy]
        if cmp_years:
            base = df[f"{max(cmp_years)} actual"]
            df[f"Δ vs {max(cmp_years)}"] = (df[df.columns[0]] / base - 1) * 100
        _show_compare_table(df, metric)
    else:
        vmin, vmax = float(piv.min().min()), float(piv.max().max())
        st.dataframe(piv.style.format("${:,.0f}").map(lambda v: _heat_color(v, vmin, vmax)),
                     use_container_width=True, height=min(560, 40 + 36 * len(piv)))
        st.caption(f"**{block_lbl} · {metric_lbl}** forecast. Greener = cheaper, redder = pricier.")
        for y in cmp_years:
            am = pd.DataFrame({h: [_actual_lookup(h, y, m, block) for m in moy] for h in hubs},
                              index=piv.index)
            st.markdown(f"##### Actual {y} — same calendar month ($/MWh)")
            st.dataframe(am.style.format("${:,.0f}"), use_container_width=True)

    # Calendar-year averages of the forecast — headline budgeting numbers.
    yr = piv.copy()
    yr["Year"] = [m[:4] for m in piv.index]
    annual = yr.groupby("Year").mean().round(0)
    st.markdown("##### Forecast calendar-year average ($/MWh)")
    st.dataframe(annual.style.format("${:,.0f}").map(
        lambda v: _heat_color(v, float(annual.min().min()), float(annual.max().max()))),
        use_container_width=True)

    st.download_button("⬇️ Download forecast matrix (CSV)", piv.to_csv(),
                       file_name=f"price_matrix_{block}_{metric}_{asof}.csv")


def _show_compare_table(df: pd.DataFrame, metric: str) -> None:
    """Single-hub forecast vs actuals, gradient on price cols, +/- on the delta."""
    price_cols = [c for c in df.columns if not c.startswith("Δ")]
    delta_cols = [c for c in df.columns if c.startswith("Δ")]
    vals = df[price_cols].to_numpy(dtype=float)
    vmin, vmax = np.nanmin(vals), np.nanmax(vals)
    fmt = {c: "${:,.0f}" for c in price_cols}
    fmt.update({c: "{:+.0f}%" for c in delta_cols})
    sty = (df.style.format(fmt, na_rep="—")
           .map(lambda v: _heat_color(v, vmin, vmax), subset=price_cols))
    if delta_cols:
        sty = sty.map(lambda v: "color:#e06666" if pd.notna(v) and v > 0
                      else ("color:#6aa84f" if pd.notna(v) else ""), subset=delta_cols)
    st.dataframe(sty, use_container_width=True, height=min(620, 40 + 36 * len(df)))
    st.caption("**Forecast** vs **actual** prices for the same calendar month in prior "
               "years. Δ = forecast above (red) / below (green) the most recent actual year.")


def _hub_compare_chart(curve: pd.DataFrame, block: str) -> go.Figure:
    sub = curve[curve["block"] == block]
    fig = go.Figure()
    for hub in sorted(sub["hub"].unique()):
        s = sub[sub["hub"] == hub].sort_values("month")
        fig.add_trace(go.Scatter(x=pd.to_datetime(s["month"]), y=s["p50"],
                                 mode="lines", name=hub))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=10),
                      title=f"P50 by hub — {block}", yaxis_title="$/MWh",
                      hovermode="x unified")
    return fig


def _gas_curve_section(asof: pd.Timestamp, horizon: int):
    """Show/refresh/edit the gas forward in-app. Returns (strip_df, source_label).

    The returned strip (resolved + mean-reverted tail, with any inline edits) is
    always passed to the forecast, so what you see here is exactly what's used.
    """
    st.subheader("⛽ Gas forward (Henry Hub)")
    has_key = bool(pf_paths.eia_api_key())

    c1, c2 = st.columns([3, 1])
    with c2:
        if has_key and st.button("🔄 Refresh from EIA"):
            try:
                with st.spinner("Pulling EIA NYMEX futures + STEO…"):
                    gas_curve.refresh_forward(horizon_months=max(horizon, 24))
                st.success("Gas curve refreshed.")
            except Exception as e:
                st.error(f"EIA refresh failed: {e}")

    use_aeo = st.checkbox(
        "Anchor the far tail to the EIA AEO long-term outlook", value=True,
        help="Beyond the quoted NYMEX/STEO strip, mean-revert toward EIA's Annual "
             "Energy Outlook Henry Hub path (year-varying, nominal $/MMBtu) instead "
             "of a flat constant. Uncheck to use the manual constant anchor below.")
    aeo_lab = ""
    if use_aeo:
        a = public_forecasts.aeo_anchor_for(pd.Timestamp(asof) + pd.DateOffset(years=4))
        aeo_lab = a[1] if a else "AEO unavailable (offline) — using constant"

    a1, a2 = st.columns(2)
    anchor = a1.number_input(
        "Long-term anchor ($/MMBtu)", min_value=1.5, max_value=10.0,
        value=float(gas_curve.LT_GAS_ANCHOR_DEFAULT), step=0.25,
        disabled=use_aeo,
        help="Fallback long-run Henry Hub level used only when the AEO anchor is off "
             "or unavailable. ~$4 reflects the post-LNG-export era (real terms).")
    revert = a2.slider(
        "Reversion speed (months)", 6, 60, int(gas_curve.REVERT_MONTHS_DEFAULT), step=6,
        help="How fast the curve fades from the last quoted price to the anchor "
             "(e-folding time). Smaller = snap to the anchor quickly; larger = "
             "hold the market's last level longer.")
    aeo_weight = st.slider(
        "AEO weight in the STEO mid-curve", 0.0, 1.0, 0.0, step=0.05,
        disabled=not use_aeo,
        help="How much the EIA AEO long-term path pulls the gas level in the months "
             "past the traded NYMEX strip but inside the STEO horizon. 0 = pure STEO "
             "(market) mid-curve; higher = blend toward EIA's long-term outlook. "
             "NYMEX near contracts are never diluted.")
    if use_aeo and aeo_lab:
        st.caption(f"Far-tail anchor source: **{aeo_lab}**.")

    strip, source = gas_curve.forward_strip(asof, horizon, lt_anchor=anchor,
                                            revert_months=revert, aeo_anchor=use_aeo,
                                            aeo_weight=aeo_weight)

    with c1:
        if has_key:
            st.caption(
                f"Source: **{source}**. Front months = traded **NYMEX Henry Hub "
                "futures** (1–4); out to ~2 years = **EIA STEO**. Beyond that the "
                "curve **mean-reverts toward your anchor with seasonality restored** "
                "(no longer a flat line). Edit any cell below to override a month.")
        else:
            st.warning("No EIA API key yet — showing a **seasonal estimate** "
                       "(reverting to your anchor). Add a free key for live futures.")
            with st.expander("Add EIA API key (free, one-time)"):
                st.markdown("Get one at https://www.eia.gov/opendata/register.php")
                k = st.text_input("EIA API key", type="password", key="eia_key_in")
                if st.button("Save key") and k:
                    pf_paths.set_eia_api_key(k)
                    st.success("Saved. Re-run to pull live gas.")
                    st.rerun()

    disp = strip.copy()
    disp["month"] = pd.to_datetime(disp["month"]).dt.strftime("%Y-%m")
    edited = st.data_editor(disp, use_container_width=True, height=240,
                            num_rows="fixed", key="gas_editor",
                            column_config={"gas": st.column_config.NumberColumn(
                                "gas ($/MMBtu)", format="%.2f")})
    out = edited.copy()
    out["month"] = pd.to_datetime(out["month"])
    label = source
    if not edited["gas"].round(4).equals(disp["gas"].round(4)):
        st.caption("✏️ Using your edited strip.")
        label = f"{source} (edited)"
    return out[["month", "gas"]], label


def _ercot_fundamentals_section() -> tuple[bool, pd.DataFrame | None]:
    """ERCOT reserve-margin scarcity overlay + EIA STEO cross-check.

    Returns ``(scarcity_on, steo_power_df)``. The CDR table is editable in-app and
    persisted back to the manual override CSV so it carries across runs.
    """
    st.subheader("🏗️ ERCOT fundamentals & cross-checks")
    st.caption("There is **no free traded ERCOT forward**, so the ERCOT-side signal "
               "is *fundamentals*: ERCOT's CDR planning reserve margins widen the "
               "scarcity tail in tight forward years (the central P50 is unchanged). "
               "The EIA STEO line is a **cross-check only — never blended**.")

    cdr = public_forecasts.ercot_reserve_margin()
    if cdr is None or cdr.empty:
        cdr = pd.DataFrame({"year": [], "reserve_margin_pct": []})
    c1, c2 = st.columns([2, 1])
    with c1:
        edited = st.data_editor(
            cdr, num_rows="dynamic", use_container_width=True, height=200, key="cdr_editor",
            column_config={
                "year": st.column_config.NumberColumn("Year", format="%d"),
                "reserve_margin_pct": st.column_config.NumberColumn(
                    "Reserve margin (%)", format="%.1f")})
    with c2:
        scarcity = st.checkbox(
            "Apply scarcity overlay", value=False,
            help="Widen the heat-rate upper tail (P90/P95) for forecast years whose "
                 "ERCOT reserve margin is below ~15%. Median P50 is left unchanged.")
        if st.button("💾 Save CDR table"):
            df = edited.dropna(subset=["year"]).copy()
            if not df.empty:
                df["year"] = df["year"].astype(int)
                hdr = ("# ERCOT reserve margins (CDR) — edited in-app. "
                       "Source: ERCOT CDR report.\n")
                pf_paths.ensure_dirs()
                (pf_paths.INPUTS_DIR / "ercot_cdr.csv").write_text(
                    hdr + df[["year", "reserve_margin_pct"]].to_csv(index=False))
                st.success("Saved to data/inputs/ercot_cdr.csv.")
        st.caption("ERCOT CDR (twice-yearly XLSX): ercot.com/gridinfo/resource")

    steo = public_forecasts.eia_steo_power()
    if steo is not None and not steo.empty:
        st.caption(f"Cross-check series: **{steo['_series'].iloc[0]}** "
                   "(EIA STEO US retail electricity, ¢/kWh → $/MWh).")
    return scarcity, steo


def render() -> None:
    st.title("⚡ ERCOT Price Forecast")
    st.caption("Market-implied heat-rate model: forward power = traded gas strip × "
               "realized heat-rate multiplier, with Monte Carlo P10/P50/P90 scenarios.")

    pf_paths.ensure_dirs()
    if pf_paths.hub_prices_parquet() is None:
        st.error("No ercot_hub_prices_15min.parquet found. Set `hub_lake_dir` in config.json.")
        return

    with st.container(border=True):
        st.header("Forecast settings")
        st.caption("🔗 Hub, as-of, horizon & simulations are shared with the Plant "
                   "Value and Wind Capture pages.")
        with st.expander("ℹ️ What do these settings do?"):
            st.markdown(
                "- **Hub** — which ERCOT trading hub to price.\n"
                "- **As of** — the forecast start; the curve runs forward from here.\n"
                "- **Horizon** — how many months out to forecast.\n"
                "- **Simulations** — Monte Carlo paths behind the P10/P50/P90 bands.\n"
                "- **Gas volatility** — how uncertain the forward gas price is.\n"
                "- **Price cap** — ceiling each simulated price is clipped to.\n"
                "- **Blend fade** — how fast traded power futures hand off to the model.\n"
                "- **8760 shape** — also build an hourly curve for settlement work.\n\n"
                "Hover the **ⓘ** next to any control for the full explanation."
            )

        all_hubs = st.checkbox(
            "Run all hubs", value=False,
            help="Forecast every ERCOT hub at once and compare them in the price "
                 "matrix. Takes a few seconds per hub.")
        # Shared "forecast context" keys (fx_*) persist these knobs across the
        # Hub's forecast pages (Price Forecast / Plant Value / Wind Capture):
        # seed each widget's default from session_state, then write the live value
        # back. Plain session_state so the standalone app needs no extra wiring.
        _fx_hub = st.session_state.get("fx_hub", "HB_NORTH")
        if all_hubs:
            hubs = list(pf_history.HUBS)
            st.caption(f"Running all {len(hubs)} hubs.")
        else:
            hubs = st.multiselect(
                "Hubs", pf_history.HUBS,
                default=[_fx_hub if _fx_hub in pf_history.HUBS else "HB_NORTH"],
                help="Pick one or more ERCOT settlement-point hubs. HB_NORTH is the "
                     "most-traded liquidity point; HB_HOUSTON/SOUTH/WEST/PAN are the "
                     "other zones; HB_HUBAVG/BUSAVG are ERCOT-wide averages.")
        if hubs:
            st.session_state["fx_hub"] = hubs[0]
        asof = st.date_input(
            "As of", value=st.session_state.get("fx_asof", pd.Timestamp.today().date()),
            help="Forecast start date. The monthly strip begins at this month and "
                 "runs forward. History before this date trains the heat rates; "
                 "nothing after it is used (so you can backtest a past 'as of').")
        st.session_state["fx_asof"] = asof
        horizon = st.slider(
            "Horizon (months)", 6, 60,
            min(max(int(st.session_state.get("fx_horizon", 36)), 6), 60), step=6,
            help="How many months forward to forecast. Gas futures are liquid ~24 "
                 "months out; beyond that the curve mean-reverts toward the EIA AEO "
                 "long-term anchor, so the far tail is outlook-driven, not market-driven.")
        st.session_state["fx_horizon"] = horizon
        _simopts = [1000, 2000, 5000, 10000]
        _fx_sims = st.session_state.get("fx_sims", 5000)
        sims = st.select_slider(
            "Simulations", _simopts, value=_fx_sims if _fx_sims in _simopts else 5000,
            help="Number of Monte Carlo price paths drawn per month. More paths = "
                 "smoother, more stable P10/P50/P90 bands but a slower run. "
                 "5,000 is plenty for monthly bands; use 10,000 for very smooth tails.")
        st.session_state["fx_sims"] = sims
        _auto_vol = public_forecasts.realized_gas_vol()
        vol_mode = st.radio(
            "Gas volatility source", ["Auto (EIA history)", "Manual"], horizontal=True,
            help="Auto derives the annualized gas log-vol from realized EIA Henry Hub "
                 "history (trailing ~5 yrs); Manual lets you set it by hand.")
        if vol_mode.startswith("Auto"):
            gas_vol = _auto_vol
            st.caption(f"Data-driven gas vol from EIA history: **{_auto_vol:.0%}** "
                       "(annualized; widens with √t over the horizon).")
        else:
            gas_vol = st.slider(
                "Gas volatility (annualized)", 0.2, 1.2, float(round(_auto_vol, 2)), step=0.05,
                help="How uncertain the forward GAS price is, as an annualized log-vol. "
                     "It widens with time (√t), so far months get wider bands. This is "
                     "the gas-side uncertainty; ERCOT heat-rate uncertainty is added on "
                     "top from history.")
        price_cap = st.number_input(
            "Price cap ($/MWh)", value=5000.0, step=500.0,
            help="Every simulated price is clipped to this ceiling — ERCOT's system-"
                 "wide offer cap (currently $5,000/MWh). It keeps the scarcity tail "
                 "realistic instead of letting a few extreme draws run to infinity.")
        fade = st.slider(
            "Power-futures blend fade (months)", 0, 36, 18, step=3,
            help="Only matters if you paste ERCOT power futures (ercot_power_strip.csv). "
                 "Near months snap to the traded price; the blend weight fades linearly "
                 "to 0 over this many months, after which it's pure gas × heat-rate. "
                 "0 = ignore traded futures entirely.")
        do_shape = st.checkbox(
            "Build 8760 hourly shape", value=False,
            help="Also spread the monthly strip into an hourly (8,760/yr) curve using "
                 "the historical hour-of-day × month price shape — for VPPA / load "
                 "settlement modeling. Slower; off by default.")
        run = st.button("Run forecast", type="primary")

    gas_override, gas_label = _gas_curve_section(pd.Timestamp(asof), horizon)
    scarcity, steo_power = _ercot_fundamentals_section()

    # Run only when the button is clicked; cache the result in session_state so
    # the display toggles below (block / scenario / comparison) re-render
    # instantly instead of re-running the Monte Carlo or vanishing.
    if run:
        if not hubs:
            st.warning("Pick at least one hub (or tick **Run all hubs**).")
            st.stop()
        prog = st.progress(0.0, text="Running Monte Carlo…")

        def _cb(i, n, h):
            prog.progress(i / n, text=f"Running {h}  ({i + 1}/{n})…")

        curve, metas = forecast.run_many(
            hubs, asof=str(asof), horizon_months=horizon, n_sims=int(sims),
            gas_vol=gas_vol, price_cap=price_cap, fade_months=fade,
            gas_override=gas_override, gas_source_label=gas_label,
            scarcity=scarcity, progress=_cb)
        prog.empty()

        hourly_by_hub = {}
        if do_shape:
            with st.spinner("Shaping 8760 hourly curves…"):
                for hh in hubs:
                    hourly_by_hub[hh] = shaping.build_8760(curve[curve.hub == hh], _rt(hh))

        st.session_state["fc"] = {
            "curve": curve, "metas": metas, "hubs": hubs, "asof": str(asof),
            "horizon": horizon, "hourly": hourly_by_hub,
            "steo_power": steo_power}

    fc = st.session_state.get("fc")
    if not fc:
        st.info("Choose your hubs and settings in the sidebar, then click "
                "**Run forecast**.")
        st.stop()

    curve, metas, hubs = fc["curve"], fc["metas"], fc["hubs"]
    asof, horizon, hourly_by_hub = fc["asof"], fc["horizon"], fc["hourly"]
    steo_power = fc.get("steo_power")
    meta = metas[0]

    cal = "✅ calibrated to traded futures" if any(m["traded_calibration"] for m in metas) else "model-only (no power strip)"
    st.success(f"{len(hubs)} hub(s): {', '.join(hubs)} • {horizon} mo • "
               f"gas: {meta['gas_source']} • {cal}")
    scar = meta.get("scarcity_overlay", {})
    st.caption(
        f"**Provenance** — gas vol: {meta.get('gas_vol')} ({meta.get('gas_vol_source')}) • "
        f"AEO anchor: {'on' if meta.get('aeo_anchor') else 'off'} • "
        f"scarcity overlay: {'on' if scar.get('scarcity') else 'off'}"
        + (f" (CDR {scar.get('cdr_years')})" if scar.get('cdr_years') else
           (f" — {scar.get('cdr')}" if scar.get('cdr') else ""))
        + ". Full sources are written into the run's `.meta.json`.")

    tab0, tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 Price matrix", "📈 Scenarios", "🔢 Strip table", "🌡️ Heat rates",
         "🎯 Calibration"])

    with tab0:
        _price_matrix_view(curve, hubs, asof)

    with tab1:
        h = hubs[0] if len(hubs) == 1 else st.selectbox(
            "Hub to chart", hubs, key="fan_hub")
        c1, c2 = st.columns(2)
        c1.plotly_chart(_fan_chart(curve[curve.hub == h], "peak", h, steo_power),
                        use_container_width=True)
        c2.plotly_chart(_fan_chart(curve[curve.hub == h], "offpeak", h, steo_power),
                        use_container_width=True)
        if len(hubs) > 1:
            st.plotly_chart(_hub_compare_chart(curve, "atc"), use_container_width=True)
        if hourly_by_hub:
            rows = [shaping.annual_summary(hourly_by_hub[hh]).assign(hub=hh) for hh in hubs]
            st.subheader("8760 annual averages (P50, $/MWh)")
            st.dataframe(pd.concat(rows)[["hub", "year", "atc", "peak", "offpeak", "hours"]].round(1),
                         use_container_width=True, hide_index=True)
            if h in hourly_by_hub:
                st.download_button(
                    f"⬇️ {h} 8760 hourly CSV", hourly_by_hub[h].to_csv(index=False),
                    file_name=f"{h}_8760_{asof}.csv", key="dl_8760")
        else:
            st.caption("Tip: tick **Build 8760 hourly shape** in the sidebar (then "
                       "re-run) for hourly curves (VPPA / settlement) and a downloadable "
                       "8760 file.")

    with tab2:
        show = curve.copy()
        show["month"] = pd.to_datetime(show["month"]).dt.strftime("%Y-%m")
        cols = ["hub", "month", "block", "gas", "ihr_p50", "p10", "p50", "p90", "mean",
                "std", "traded", "blend_w"]
        st.dataframe(show[[c for c in cols if c in show.columns]].round(2),
                     use_container_width=True, height=480, hide_index=True)
        st.download_button("⬇️ Download full strip CSV", show.to_csv(index=False),
                           file_name=f"forecast_{'_'.join(hubs) if len(hubs) <= 3 else 'multi'}_{asof}.csv")

    with tab3:
        import heat_rate
        if len(hubs) > 1:
            hh = st.selectbox("Hub", hubs, key="hr_hub")
        else:
            hh = hubs[0]
        rt = _rt(hh)

        st.markdown(
            "#### What is the implied heat rate?\n"
            "It's the engine's core **multiplier** — how much gas the ERCOT market "
            "effectively pays for each MWh of power:\n\n"
            "$$\\text{heat rate}\\;(\\text{MMBtu/MWh}) = "
            "\\frac{\\text{hub power price}\\;(\\$/\\text{MWh})}"
            "{\\text{Henry Hub gas}\\;(\\$/\\text{MMBtu})}$$\n\n"
            "We measure it from **your own RTM history** for every "
            "*(month × peak / off-peak)* bucket across all years, then forecast "
            "forward as **gas strip × heat rate**. So the gas curve sets the "
            "*level*; the heat rate carries ERCOT's *shape, congestion and scarcity*."
        )
        st.markdown(
            "- **~7–10** → calm, gas-driven market (power ≈ the cost of burning gas).\n"
            "- **12–18** → tighter conditions: summer demand, low wind/solar, congestion.\n"
            "- **30+** → scarcity pricing — the grid is short and prices spike toward the cap.\n"
            "- **Peak > Off-peak**, because demand and scarcity concentrate in the "
            "5×16 on-peak block (weekdays, hours-ending 7–22)."
        )

        st.plotly_chart(_heat_rate_chart(rt), use_container_width=True)

        st.markdown("##### Heat rate by month — what the forecast uses")
        st.caption("The **median** is the central multiplier behind each P50 price. "
                   "The **P10–P90** band is the year-to-year spread that the Monte "
                   "Carlo samples to build the scenario fan. **Mean** is shown only "
                   "to expose scarcity-year skew (see Uri below).")
        st.dataframe(heat_rate.display_table(rt), use_container_width=True, hide_index=True)

        # Data-driven Winter Storm Uri callout.
        b = heat_rate.buckets(rt).set_index(["month", "block"])
        if (2, "peak") in b.index:
            f = b.loc[(2, "peak")]
            st.info(
                f"❄️ **Why median, not mean?** February peak heat rate: "
                f"median **{f['ihr_p50']:.1f}** vs mean **{f['ihr_mean']:.1f}** "
                f"(P90 **{f['ihr_p90']:.0f}**). That gap is **Winter Storm Uri "
                f"(Feb 2021)**, when prices pinned the cap. Anchoring on the median "
                f"keeps the base case sane, while Uri still shows up in the P90 / "
                f"scenario tails where it belongs — instead of permanently inflating "
                f"every February forecast."
            )

    with tab4:
        _calibration_view(hubs[0] if len(hubs) == 1 else
                          st.selectbox("Hub to backtest", hubs, key="bt_hub"), horizon)

    saved = []
    for hub_i, m in zip(hubs, metas):
        forecast_store.save(curve[curve.hub == hub_i], m, hourly_by_hub.get(hub_i))
        saved.append(hub_i)
    st.caption(f"💾 Saved {len(saved)} forecast(s) to `{pf_paths.FORECASTS_DIR}` "
               f"({', '.join(saved)}).")
