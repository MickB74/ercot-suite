"""Render the "This month & next — weather forecast" tab in each portal.

Each portal calls :func:`render_near_term_tab` after setting up its hub, analytics,
contract, and branding imports. The function is self-contained: it fetches weather,
calibrates against SCED, merges actual + forecast daily data, and renders the chart
and KPIs.

Usage::

    from ercot_core.near_term_bill import render_near_term_tab

    render_near_term_tab(
        st=st,
        a=contract.ASSET,
        hub=hub,
        analytics=analytics,
        branding=branding,
        terms=terms,
        win_start=win_start,
        win_end=win_end,
        hist_mwh=hist_mwh,          # pd.Series indexed 1-12 (historical shape)
        fwd_price=fwd_price,         # forward market price $/MWh from the sidebar
        gen_kwargs=None,             # extra kwargs for hub.generation() (Azure Sky multi-unit)
    )
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go

from . import gen_forecast as gf
from . import weather_forecast as wf


def render_near_term_tab(
    st,
    *,
    a: dict,
    hub,
    analytics,
    branding,
    terms: dict,
    win_start,
    win_end,
    hist_mwh: pd.Series,
    fwd_price: float,
    gen_kwargs: dict | None = None,
) -> None:
    """Render the near-term weather bill forecast tab.

    Parameters
    ----------
    st:
        The Streamlit module (passed in so this module doesn't import it at the
        top level, keeping it usable outside a Streamlit context).
    a:
        Portal ASSET dict (must have lat, lon, tech, capacity_mw; wind also
        needs hub_height_m).
    hub:
        Portal-specific hub module.
    analytics:
        Portal-specific analytics module.
    branding:
        Portal-specific branding module.
    terms:
        Loaded contract terms dict.
    win_start, win_end:
        Settlement data window (dates).
    hist_mwh:
        Historical mean monthly MWh indexed 1–12, at the contracted share.
        Used as fallback for days beyond the weather forecast horizon.
    fwd_price:
        Forward market price assumption ($/MWh) — usually set in the sidebar.
    gen_kwargs:
        Extra kwargs forwarded to ``hub.generation()`` — only needed for
        Azure Sky (``{"units": a["units"]}``).
    """
    strike = float(terms.get("strike", 0.0))
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0
    cap_share = float(a["capacity_mw"]) * share
    is_wind = "wind" in str(a.get("tech", "")).lower()
    tech = "wind" if is_wind else "solar"
    hub_h = float(a.get("hub_height_m") or 90.0)
    gen_kwargs = gen_kwargs or {}

    today_ct = pd.Timestamp.now("America/Chicago")
    month_start_ct = today_ct.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month_start = month_start_ct + pd.offsets.MonthBegin(1)
    next_month_end_date = (next_month_start + pd.offsets.MonthEnd(1)).date()
    cur_month_str = month_start_ct.strftime("%Y-%m")
    next_month_str = next_month_start.strftime("%Y-%m")

    # ── fetch weather ────────────────────────────────────────────────────────
    @st.cache_data(show_spinner="Fetching weather forecast…", ttl=7200)
    def _weather(lat, lon, tech_key):
        try:
            return wf.fetch(lat, lon, tech_key, past_days=60, forecast_days=16), None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)

    weather_df, wfail = _weather(float(a["lat"]), float(a["lon"]), tech)

    if wfail:
        st.warning(
            f"⚠️ Weather forecast unavailable ({wfail}). "
            "Check your internet connection or try again shortly."
        )
        return

    # ── calibrate against SCED ───────────────────────────────────────────────
    # gen_kwargs snapshot for the closure (Streamlit can't cache mutable dicts directly)
    _units = list(gen_kwargs.get("units", []))  # empty list = single-unit portal
    _is_multi_unit = bool(_units)

    @st.cache_data(show_spinner="Calibrating against SCED history…", ttl=3600)
    def _calibrate(lat, lon, tech_key, win_end_str, rnode, units_tuple):
        w, err = _weather(lat, lon, tech_key)
        if w is None:
            return 1.0, 0
        win_end_d = dt.date.fromisoformat(win_end_str)
        cal_start = max(
            win_start if isinstance(win_start, dt.date) else pd.Timestamp(win_start).date(),
            win_end_d - dt.timedelta(days=60),
        )
        if cal_start >= win_end_d:
            return 1.0, 0
        t_start = pd.Timestamp(cal_start)
        t_end = pd.Timestamp(win_end_d) + pd.Timedelta(days=1)
        if units_tuple:
            # Multi-unit portal (Azure Sky): positional units arg
            gen_raw = hub.generation(rnode, list(units_tuple), t_start, t_end)
        else:
            gen_raw = hub.generation(rnode, t_start, t_end)
        if gen_raw.empty:
            return 1.0, 0
        gen_raw = gen_raw.copy()
        gen_raw["mwh"] = gen_raw.get("mwh", gen_raw["mw"] * 0.25)  # 15-min → MWh
        gen_raw["date"] = pd.to_datetime(gen_raw["interval_start"]).dt.date
        sced_daily = gen_raw.groupby("date")["mwh"].sum() * share
        factor = gf.calibrate(w, sced_daily, cap_share, tech_key, hub_height_m=hub_h)
        return factor, int(len(sced_daily))

    cal_factor, n_cal_days = _calibrate(
        float(a["lat"]), float(a["lon"]), tech,
        str(win_end if isinstance(win_end, dt.date) else pd.Timestamp(win_end).date()),
        a["resource_node"],
        tuple(_units),
    )

    # ── current month actuals ─────────────────────────────────────────────────
    actual_rows: list[dict] = []
    win_end_date = win_end if isinstance(win_end, dt.date) else pd.Timestamp(win_end).date()
    cur_month_start_date = month_start_ct.date()

    if win_end_date >= cur_month_start_date:
        @st.cache_data(show_spinner="Loading current-month actuals…", ttl=1800)
        def _actuals(start_str, end_str, tkey):
            s = dt.date.fromisoformat(start_str)
            e = dt.date.fromisoformat(end_str)
            res = analytics.settle(s, e, terms)
            if res is None or res["intervals"].empty:
                return pd.DataFrame()
            df = res["intervals"].copy()
            df["date"] = pd.to_datetime(df["interval_start"]).dt.date
            return df.groupby("date").agg(
                mwh=("mwh", "sum"),
                net=("cfd", "sum"),
                price=("price", "mean"),
            ).reset_index()

        da = _actuals(
            str(cur_month_start_date),
            str(min(win_end_date, today_ct.date())),
            tuple(sorted(terms.items())),
        )
        for _, row in da.iterrows():
            actual_rows.append({
                "date": row["date"], "mwh": row["mwh"],
                "net": row["net"], "kind": "actual",
            })

    settled_dates = {r["date"] for r in actual_rows}

    # ── weather-forecast days ─────────────────────────────────────────────────
    daily_fcast = gf.daily_forecast_mwh(
        weather_df, tech, cap_share,
        hub_height_m=hub_h, cal_factor=cal_factor,
    )
    weather_max_date = max(daily_fcast.index) if len(daily_fcast) > 0 else today_ct.date()

    forecast_rows: list[dict] = []
    for d_date, mwh in daily_fcast.items():
        if d_date < cur_month_start_date or d_date in settled_dates:
            continue
        if d_date > next_month_end_date:
            continue
        net = float(mwh) * (fwd_price - strike)
        kind = "forecast_cur" if d_date < next_month_start.date() else "forecast_next"
        forecast_rows.append({"date": d_date, "mwh": float(mwh), "net": net, "kind": kind})

    # Fill days beyond weather horizon with historical shape
    d = weather_max_date + dt.timedelta(days=1)
    while d <= next_month_end_date:
        if d not in settled_dates and not any(r["date"] == d for r in forecast_rows):
            mwh = gf.hist_mwh_for_date(d, hist_mwh)
            net = mwh * (fwd_price - strike)
            kind = "forecast_cur" if d < next_month_start.date() else "forecast_next"
            forecast_rows.append({"date": d, "mwh": mwh, "net": net, "kind": "hist_" + kind.split("_", 1)[1]})
        d += dt.timedelta(days=1)

    all_rows = actual_rows + forecast_rows
    if not all_rows:
        st.info("No data available for near-term projection.")
        return

    all_df = pd.DataFrame(all_rows).sort_values("date").reset_index(drop=True)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    actual_mask = all_df["kind"] == "actual"
    cur_mask = all_df["date"].apply(lambda d: str(d)[:7] == cur_month_str)
    next_mask = all_df["date"].apply(lambda d: str(d)[:7] == next_month_str)

    mtd_mwh = float(all_df.loc[actual_mask, "mwh"].sum())
    mtd_net = float(all_df.loc[actual_mask, "net"].sum())
    proj_cur = float(all_df.loc[cur_mask, "net"].sum())
    next_net = float(all_df.loc[next_mask, "net"].sum())
    next_mwh = float(all_df.loc[next_mask, "mwh"].sum())

    n_settled = int(actual_mask.sum())
    n_fcast_cur = int((all_df["kind"].str.startswith("forecast_cur") | all_df["kind"].str.startswith("hist_cur")).sum())
    n_fcast_next = int((all_df["kind"].str.startswith("forecast_next") | all_df["kind"].str.startswith("hist_next")).sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        f"MTD actual ({cur_month_str})",
        branding.signed_money(mtd_net),
        delta=f"{mtd_mwh:,.0f} MWh · {n_settled} days settled",
        delta_color="off",
    )
    k2.metric(
        f"Projected month-end",
        branding.signed_money(proj_cur),
        delta=f"{n_fcast_cur} forecast days remaining",
        delta_color="off",
    )
    k3.metric(
        f"{next_month_str} estimate",
        branding.signed_money(next_net),
        delta=f"{next_mwh:,.0f} MWh · {n_fcast_next} days",
        delta_color="off",
    )
    k4.metric(
        "Cal. factor",
        f"{cal_factor:.3f}",
        delta=f"from {n_cal_days} SCED days" if n_cal_days else "no overlap — uncalibrated",
        delta_color="off",
    )

    # ── chart ─────────────────────────────────────────────────────────────────
    SOLID_POS = branding.GOOD
    SOLID_NEG = branding.BAD
    FCAST_CUR_POS = "rgba(136,169,24,0.55)"
    FCAST_CUR_NEG = "rgba(178,58,72,0.50)"
    FCAST_NEXT_POS = "rgba(84,164,218,0.70)"
    FCAST_NEXT_NEG = "rgba(178,58,72,0.40)"
    HIST_POS = "rgba(84,164,218,0.40)"
    HIST_NEG = "rgba(178,58,72,0.30)"

    def _bar_color(row) -> str:
        pos = row["net"] >= 0
        k = row["kind"]
        if k == "actual":
            return SOLID_POS if pos else SOLID_NEG
        if k in ("forecast_cur", "hist_cur"):
            return FCAST_CUR_POS if pos else FCAST_CUR_NEG
        return FCAST_NEXT_POS if pos else FCAST_NEXT_NEG

    bar_colors = [_bar_color(r) for _, r in all_df.iterrows()]
    x_labels = [str(r["date"]) for _, r in all_df.iterrows()]

    fig = go.Figure()

    # ── settlement bars (primary y-axis) ─────────────────────────────────────
    fig.add_bar(x=[], y=[], name="Settled", marker_color=SOLID_POS, showlegend=True)
    fig.add_bar(x=[], y=[], name=f"Forecast – {cur_month_str}", marker_color=FCAST_CUR_POS, showlegend=True)
    fig.add_bar(x=[], y=[], name=f"Forecast – {next_month_str}", marker_color=FCAST_NEXT_POS, showlegend=True)

    fig.add_bar(
        x=x_labels,
        y=all_df["net"].tolist(),
        marker_color=bar_colors,
        showlegend=False,
        yaxis="y1",
        hovertemplate="%{x}<br>Net: $%{y:,.0f}<extra></extra>",
    )

    # ── generation line (secondary y-axis) ───────────────────────────────────
    # Solid line for settled days, dashed for forecast
    settled_mwh = [v if k == "actual" else None for v, k in zip(all_df["mwh"], all_df["kind"])]
    fcast_mwh   = [v if k != "actual" else None for v, k in zip(all_df["mwh"], all_df["kind"])]

    fig.add_scatter(
        x=x_labels, y=settled_mwh,
        mode="lines+markers",
        line=dict(color="rgba(0,105,179,0.85)", width=2),
        marker=dict(size=4),
        name="Generation (settled)",
        yaxis="y2",
        connectgaps=False,
        hovertemplate="%{x}<br>Gen: %{y:,.0f} MWh<extra></extra>",
    )
    fig.add_scatter(
        x=x_labels, y=fcast_mwh,
        mode="lines+markers",
        line=dict(color="rgba(0,105,179,0.45)", width=2, dash="dot"),
        marker=dict(size=3),
        name="Generation (forecast)",
        yaxis="y2",
        connectgaps=False,
        hovertemplate="%{x}<br>Gen: %{y:,.0f} MWh<extra></extra>",
    )

    # Month-boundary divider
    bdy_str = next_month_start.strftime("%Y-%m-%d")
    fig.add_vline(x=bdy_str, line_dash="dot", line_color="#848484", line_width=1.5)
    fig.add_annotation(
        x=bdy_str, y=1.06, yref="paper",
        text=f"← {cur_month_str}   {next_month_str} →",
        showarrow=False, font=dict(size=10, color="#848484"), xanchor="center",
    )

    fig.update_layout(
        height=400,
        hovermode="x unified",
        margin=dict(t=30, b=10),
        yaxis=dict(
            title="Daily net settlement ($)",
            zeroline=True, zerolinecolor="#ddd",
            side="left",
        ),
        yaxis2=dict(
            title="Daily generation (MWh)",
            overlaying="y",
            side="right",
            showgrid=False,
            rangemode="tozero",
            tickfont=dict(color="rgba(0,105,179,0.8)"),
            title_font=dict(color="rgba(0,105,179,0.8)"),
        ),
        legend=dict(orientation="h", y=1.14),
        bargap=0.15,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── detail table ──────────────────────────────────────────────────────────
    with st.expander("Daily detail"):
        show = all_df[["date", "mwh", "net", "kind"]].copy()
        show["date"] = show["date"].astype(str)
        show["mwh"] = show["mwh"].map(lambda v: f"{v:,.1f}")
        show["net"] = show["net"].map(branding.signed_money_raw)
        kind_labels = {
            "actual": "Settled",
            "forecast_cur": f"Forecast – {cur_month_str}",
            "forecast_next": f"Forecast – {next_month_str}",
            "hist_cur": f"Hist. shape – {cur_month_str}",
            "hist_next": f"Hist. shape – {next_month_str}",
        }
        show["kind"] = show["kind"].map(kind_labels).fillna(show["kind"])
        show.columns = ["Date", "MWh", "Net ($)", "Source"]
        st.dataframe(show, hide_index=True, use_container_width=True)

    # ── footnote ─────────────────────────────────────────────────────────────
    src = "Open-Meteo free API (no key required)"
    st.caption(
        f"**Source:** {src}. "
        f"**Cal. factor {cal_factor:.3f}** — weather-model output scaled to match "
        f"{'last ' + str(n_cal_days) + ' days of SCED history' if n_cal_days else 'no SCED history (uncalibrated)'}. "
        f"Days beyond the 16-day forecast horizon use the historical monthly shape. "
        f"Forward price: **\\${fwd_price:,.2f}/MWh** · Strike: **\\${strike:,.2f}/MWh**."
    )
