"""Shared Generation Analysis page renderer for ERCOT settlement portals.

Provides :func:`render`, a self-contained Streamlit page that shows:

1. KPI row (total MWh, avg monthly MWh, capacity factor, months of data)
2. Monthly generation bar chart with capacity-factor color gradient + overlay
3. Daily generation calendar heatmap
4. Average hourly production profile by season
5. Year-over-year monthly comparison table

Usage::

    from ercot_core.generation_analysis import render

    render(
        st=st,
        a=contract.ASSET,
        hub=hub,
        analytics=analytics,
        branding=branding,
        contract=contract,
        terms=terms,
        win_start=win_start,
        win_end=win_end,
    )
"""

from __future__ import annotations

import calendar
import datetime as dt
import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOURS_PER_INTERVAL = 0.25  # ERCOT 15-min intervals


def _eom(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _last_full_month(we: dt.date) -> tuple[int, int]:
    _, last_day = calendar.monthrange(we.year, we.month)
    if we.day >= last_day:
        return we.year, we.month
    prev = we.replace(day=1) - dt.timedelta(days=1)
    return prev.year, prev.month


def _season(month: int) -> str:
    if month in (6, 7, 8, 9):
        return "Summer"
    if month in (11, 12, 1, 2):
        return "Winter"
    return "Shoulder"


def _cf_color(cf: float, good: str, bad: str, accent: str) -> str:
    """Return a hex color on a red-yellow-green gradient based on CF."""
    if cf < 0.15:
        return bad
    if cf > 0.35:
        return good
    # interpolate through accent
    if cf < 0.25:
        t = (cf - 0.15) / 0.10
        return _lerp_hex(bad, accent, t)
    t = (cf - 0.25) / 0.10
    return _lerp_hex(accent, good, t)


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    """Linear interpolation between two hex colors."""
    c1 = c1.lstrip("#")
    c2 = c2.lstrip("#")
    r = int(int(c1[0:2], 16) * (1 - t) + int(c2[0:2], 16) * t)
    g = int(int(c1[2:4], 16) * (1 - t) + int(c2[2:4], 16) * t)
    b = int(int(c1[4:6], 16) * (1 - t) + int(c2[4:6], 16) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_generation(hub, resource_node: str, start: dt.date, end: dt.date,
                     gen_kwargs: dict | None = None) -> pd.DataFrame:
    """Fetch interval generation data from the hub."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) + pd.Timedelta(days=1)
    kw = gen_kwargs or {}
    df = hub.generation(resource_node, start_ts, end_ts, **kw)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["interval_start"] = pd.to_datetime(df["interval_start"])
    if "mwh" not in df.columns and "mw" in df.columns:
        df["mwh"] = df["mw"] * HOURS_PER_INTERVAL
    return df


# ---------------------------------------------------------------------------
# Period picker (sidebar)
# ---------------------------------------------------------------------------

def _period_picker(st, win_start: dt.date, win_end: dt.date):
    """Render Month/Quarter/Year/Custom radio in sidebar; return (start, end)."""
    st.sidebar.header("Period")
    mode = st.sidebar.radio(
        "Period type",
        ["Month", "Quarter", "Year", "Custom"],
        horizontal=True,
        key="gen_analysis_period",
    )
    years = list(range(win_end.year, win_start.year - 1, -1))
    _lfy, _lfm = _last_full_month(win_end)

    if mode == "Month":
        c1, c2 = st.sidebar.columns(2)
        yr_def = years.index(_lfy) if _lfy in years else 0
        yr = c1.selectbox("Year", years, index=yr_def, key="gen_yr_m")
        mo = c2.selectbox(
            "Month", list(range(1, 13)), index=_lfm - 1,
            format_func=lambda m: dt.date(2000, m, 1).strftime("%b"),
            key="gen_mo",
        )
        start_d, end_d = dt.date(yr, mo, 1), _eom(yr, mo)
    elif mode == "Quarter":
        c1, c2 = st.sidebar.columns(2)
        yr = c1.selectbox("Year", years, key="gen_yr_q")
        q = c2.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"], key="gen_q")
        sm = (int(q[1]) - 1) * 3 + 1
        start_d, end_d = dt.date(yr, sm, 1), _eom(yr, sm + 2)
    elif mode == "Year":
        yr = st.sidebar.selectbox("Year", years, key="gen_yr_y")
        start_d, end_d = dt.date(yr, 1, 1), dt.date(yr, 12, 31)
    else:
        c1, c2 = st.sidebar.columns(2)
        start_d = c1.date_input(
            "Start", value=win_start, min_value=win_start, max_value=win_end,
            key="gen_start",
        )
        end_d = c2.date_input(
            "End", value=win_end, min_value=win_start, max_value=win_end,
            key="gen_end",
        )

    start_d = max(start_d, win_start)
    end_d = min(end_d, win_end)
    return start_d, end_d


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def _render_kpis(st, gen: pd.DataFrame, capacity_mw: float, share: float,
                 branding) -> None:
    """KPI row: total MWh, avg monthly, capacity factor, months."""
    gen = gen.copy()
    gen["month"] = gen["interval_start"].dt.to_period("M")
    monthly = gen.groupby("month")["mwh"].sum()
    n_months = len(monthly)
    total_mwh = monthly.sum()
    avg_monthly = total_mwh / n_months if n_months else 0.0

    # Capacity factor: actual MWh / (capacity * hours in window * share)
    first = gen["interval_start"].min()
    last = gen["interval_start"].max()
    hours_in_range = (last - first).total_seconds() / 3600.0 + HOURS_PER_INTERVAL
    max_mwh = capacity_mw * share * hours_in_range
    cf = total_mwh / max_mwh if max_mwh > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total MWh", f"{total_mwh:,.0f}")
    c2.metric("Avg Monthly MWh", f"{avg_monthly:,.0f}")
    c3.metric("Capacity Factor", f"{cf:.1%}")
    c4.metric("Months of Data", f"{n_months}")


def _render_monthly_bar(st, gen: pd.DataFrame, capacity_mw: float,
                        share: float, branding) -> None:
    """Monthly generation bars colored by CF + capacity-factor line overlay."""
    gen = gen.copy()
    gen["ym"] = gen["interval_start"].dt.to_period("M")
    monthly = gen.groupby("ym").agg(
        mwh=("mwh", "sum"),
        intervals=("mwh", "count"),
    ).reset_index()
    monthly["hours"] = monthly["intervals"] * HOURS_PER_INTERVAL
    monthly["cf"] = monthly["mwh"] / (capacity_mw * share * monthly["hours"])
    monthly["cf"] = monthly["cf"].clip(0, 1)
    monthly["label"] = monthly["ym"].astype(str)

    bar_colors = [
        _cf_color(cf, branding.GOOD, branding.BAD, branding.ACCENT)
        for cf in monthly["cf"]
    ]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=monthly["label"],
        y=monthly["mwh"],
        marker_color=bar_colors,
        name="MWh",
        yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=monthly["label"],
        y=monthly["cf"] * 100,
        mode="lines+markers",
        line=dict(color=branding.ACCENT, width=2),
        marker=dict(size=5),
        name="Capacity Factor %",
        yaxis="y2",
    ))

    # Year-over-year dashed overlay if 2+ years
    unique_years = monthly["ym"].apply(lambda p: p.year).unique()
    if len(unique_years) >= 2:
        prior_year = sorted(unique_years)[-2]
        prior = monthly[monthly["ym"].apply(lambda p: p.year) == prior_year]
        if not prior.empty:
            prior_x = [
                str(monthly["ym"].iloc[-1].year) + "-"
                + f"{p.month:02d}" for p in prior["ym"]
            ]
            fig.add_trace(go.Scatter(
                x=prior_x,
                y=prior["mwh"],
                mode="lines",
                line=dict(color="gray", width=1, dash="dash"),
                name=f"{prior_year} (prior year)",
                opacity=0.5,
                yaxis="y",
            ))

    fig.update_layout(
        title="Monthly Generation",
        xaxis_title="Month",
        yaxis=dict(title="MWh", side="left"),
        yaxis2=dict(
            title="Capacity Factor %",
            side="right",
            overlaying="y",
            range=[0, 100],
            showgrid=False,
        ),
        legend=dict(orientation="h", y=-0.15),
        height=420,
        margin=dict(t=40, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_daily_heatmap(st, gen: pd.DataFrame, branding) -> None:
    """Calendar-style heatmap: x = day-of-month, y = year-month, color = MWh."""
    gen = gen.copy()
    gen["date"] = gen["interval_start"].dt.date
    daily = gen.groupby("date")["mwh"].sum().reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["day"] = daily["date"].dt.day
    daily["ym"] = daily["date"].dt.to_period("M").astype(str)

    # Build a full grid for all year-month x day combos to highlight gaps
    ym_list = sorted(daily["ym"].unique())
    pivot = daily.pivot_table(index="ym", columns="day", values="mwh", aggfunc="sum")
    pivot = pivot.reindex(index=ym_list, columns=range(1, 32))

    # Missing / zero days as NaN for gap display
    z = pivot.values
    text = np.where(np.isnan(z), "No data", np.vectorize(lambda v: f"{v:,.0f}")(z))

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=[str(d) for d in range(1, 32)],
        y=ym_list,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=8),
        colorscale=[
            [0.0, branding.BAD],
            [0.5, branding.ACCENT],
            [1.0, branding.GOOD],
        ],
        colorbar=dict(title="MWh"),
        hoverongaps=True,
        hovertemplate="Day %{x}, %{y}<br>%{z:,.0f} MWh<extra></extra>",
    ))

    fig.update_layout(
        title="Daily Generation Heatmap",
        xaxis_title="Day of Month",
        yaxis_title="Month",
        yaxis=dict(autorange="reversed"),
        height=max(250, len(ym_list) * 28 + 80),
        margin=dict(t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_hourly_profile(st, gen: pd.DataFrame, tech: str,
                           branding) -> None:
    """Average hourly generation shape by season."""
    gen = gen.copy()
    gen["hour"] = gen["interval_start"].dt.hour
    gen["month"] = gen["interval_start"].dt.month
    gen["season"] = gen["month"].map(_season)

    hourly = gen.groupby(["season", "hour"])["mw"].mean().reset_index()

    season_colors = {
        "Summer": branding.BAD,
        "Winter": branding.ACCENT,
        "Shoulder": branding.GOOD,
    }

    fig = go.Figure()
    for season in ["Summer", "Winter", "Shoulder"]:
        s = hourly[hourly["season"] == season]
        if s.empty:
            continue
        fig.add_trace(go.Scatter(
            x=s["hour"],
            y=s["mw"],
            mode="lines+markers",
            name=season,
            line=dict(color=season_colors.get(season, "#888"), width=2),
            marker=dict(size=4),
        ))

    tech_label = "solar bell curve" if "solar" in tech.lower() else "diurnal pattern"
    fig.update_layout(
        title=f"Average Hourly Profile ({tech_label})",
        xaxis=dict(title="Hour of Day (CT)", dtick=1, range=[-0.5, 23.5]),
        yaxis=dict(title="Average MW"),
        legend=dict(orientation="h", y=-0.15),
        height=380,
        margin=dict(t=40, b=60),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_yoy_table(st, gen: pd.DataFrame, branding) -> pd.DataFrame:
    """Year-over-year monthly pivot table with color coding."""
    gen = gen.copy()
    gen["year"] = gen["interval_start"].dt.year
    gen["month"] = gen["interval_start"].dt.month
    monthly = gen.groupby(["year", "month"])["mwh"].sum().reset_index()

    pivot = monthly.pivot_table(index="month", columns="year", values="mwh")
    pivot.index = [dt.date(2000, m, 1).strftime("%b") for m in pivot.index]
    pivot.columns = [str(int(y)) for y in pivot.columns]

    # Add total row
    totals = pivot.sum(axis=0)
    totals.name = "Total"
    pivot = pd.concat([pivot, totals.to_frame().T])

    st.subheader("Year-over-Year Monthly Comparison (MWh)")

    # Style: highlight high/low
    def _color_cells(val):
        if pd.isna(val):
            return ""
        return ""

    styled = pivot.style.format("{:,.0f}", na_rep="—").background_gradient(
        cmap="RdYlGn", axis=None, subset=pivot.index[:-1],
    )
    st.dataframe(styled, use_container_width=True)
    return pivot


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(
    st,
    *,
    a: dict,
    hub,
    analytics,
    branding,
    contract,
    terms: dict,
    win_start,
    win_end,
) -> None:
    """Render the Generation Analysis page.

    Parameters
    ----------
    st :
        The Streamlit module (passed in, not imported at module level).
    a :
        Portal ASSET dict (capacity_mw, tech, resource_node, hub, etc.).
    hub :
        Portal hub module with ``generation()`` and ``settlement_window()``.
    analytics :
        Portal analytics module.
    branding :
        Portal branding module (hero, footer, GOOD, BAD, ACCENT).
    contract :
        Portal contract module.
    terms :
        Loaded contract terms dict.
    win_start, win_end :
        Settlement data window dates.
    """
    resource_node = a.get("resource_node", "")
    capacity_mw = float(a.get("capacity_mw", 0))
    tech = str(a.get("tech", "solar")).lower()
    share = float(terms.get("volume_share_pct", 100.0)) / 100.0

    branding.hero(
        st,
        "Generation Analysis",
        f"{resource_node} · {capacity_mw:.0f} MW {tech}",
    )

    # ── guard: empty window ─────────────────────────────────────────────────
    if win_start is None or win_end is None:
        st.info("No generation data available for this asset yet.")
        branding.footer(st)
        return

    # ── period picker ───────────────────────────────────────────────────────
    period_start, period_end = _period_picker(st, win_start, win_end)

    # ── load generation data ────────────────────────────────────────────────
    gen_kwargs = {}
    if "units" in a:
        gen_kwargs["units"] = a["units"]

    gen = _load_generation(hub, resource_node, period_start, period_end,
                           gen_kwargs=gen_kwargs)
    if gen.empty:
        st.info(
            f"No generation data found for {resource_node} "
            f"between {period_start} and {period_end}."
        )
        branding.footer(st)
        return

    # Apply volume share
    gen["mwh"] = gen["mwh"] * share
    if "mw" in gen.columns:
        gen["mw"] = gen["mw"] * share
    else:
        gen["mw"] = gen["mwh"] / HOURS_PER_INTERVAL

    # ── load full-window data for history charts ────────────────────────────
    gen_full = _load_generation(hub, resource_node, win_start, win_end,
                                gen_kwargs=gen_kwargs)
    if not gen_full.empty:
        gen_full["mwh"] = gen_full["mwh"] * share
        if "mw" in gen_full.columns:
            gen_full["mw"] = gen_full["mw"] * share
        else:
            gen_full["mw"] = gen_full["mwh"] / HOURS_PER_INTERVAL
    else:
        gen_full = gen

    # ── 1. KPI row (full history) ───────────────────────────────────────────
    _render_kpis(st, gen_full, capacity_mw, share, branding)
    st.divider()

    # ── 2. Monthly generation bar chart (full history) ──────────────────────
    _render_monthly_bar(st, gen_full, capacity_mw, share, branding)

    # ── 3. Daily generation heatmap (selected period) ───────────────────────
    st.subheader("Daily Generation Heatmap")
    _render_daily_heatmap(st, gen, branding)

    # ── 4. Hourly production profile (selected period) ──────────────────────
    _render_hourly_profile(st, gen, tech, branding)

    # ── 5. Year-over-year table (full history) ──────────────────────────────
    yoy_pivot = _render_yoy_table(st, gen_full, branding)

    # ── download expander ───────────────────────────────────────────────────
    with st.expander("Download data"):
        # Monthly summary
        gen_dl = gen_full.copy()
        gen_dl["month"] = gen_dl["interval_start"].dt.to_period("M").astype(str)
        monthly_dl = gen_dl.groupby("month").agg(
            MWh=("mwh", "sum"),
            intervals=("mwh", "count"),
        ).reset_index()
        monthly_dl["hours"] = monthly_dl["intervals"] * HOURS_PER_INTERVAL
        monthly_dl["capacity_factor"] = (
            monthly_dl["MWh"] / (capacity_mw * share * monthly_dl["hours"])
        ).clip(0, 1)
        monthly_dl = monthly_dl.drop(columns=["intervals", "hours"])

        buf = io.BytesIO()
        monthly_dl.to_csv(buf, index=False)
        st.download_button(
            "Monthly generation CSV",
            data=buf.getvalue(),
            file_name=f"{resource_node}_monthly_generation.csv",
            mime="text/csv",
        )

        # YoY pivot
        buf2 = io.BytesIO()
        yoy_pivot.to_csv(buf2)
        st.download_button(
            "Year-over-year CSV",
            data=buf2.getvalue(),
            file_name=f"{resource_node}_yoy_generation.csv",
            mime="text/csv",
        )

    branding.footer(st)
