"""Capture Rate Analysis page for ERCOT settlement portals.

Renders a full capture-rate diagnostic: KPI row (capture price, mean grid
price, capture ratio, basis spread), monthly capture trend chart, hourly
price-generation correlation by season, capture heatmap (hour x month), and
a price-generation scatter.  A sidebar period picker (Month / Quarter / Year /
Custom) controls the analysis window.

Usage::

    from ercot_core.capture_analysis import render

    render(
        st,
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

import numpy as np
import pandas as pd
import plotly.graph_objects as go


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _price_col(df: pd.DataFrame) -> str:
    """Resolve whichever price column the frame carries."""
    return next(
        (c for c in ("spp", "settlement_point_price", "price") if c in df.columns),
        df.columns[-1],
    )


def _eom(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _last_full_month(we: dt.date) -> tuple[int, int]:
    _, last_day = calendar.monthrange(we.year, we.month)
    if we.day >= last_day:
        return we.year, we.month
    prev = we.replace(day=1) - dt.timedelta(days=1)
    return prev.year, prev.month


def _season(month: int) -> str:
    if month in (6, 7, 8):
        return "Summer"
    if month in (12, 1, 2):
        return "Winter"
    return "Shoulder"


# ---------------------------------------------------------------------------
# Period picker (sidebar)
# ---------------------------------------------------------------------------

def _period_picker(st, win_start: dt.date, win_end: dt.date):
    """Render the sidebar period selector; return (start_date, end_date)."""
    st.sidebar.header("Period")
    mode = st.sidebar.radio(
        "Period type",
        ["Month", "Quarter", "Year", "Custom"],
        horizontal=True,
        key="cap_period_mode",
    )
    years = list(range(win_end.year, win_start.year - 1, -1))
    _lfy, _lfm = _last_full_month(win_end)

    if mode == "Month":
        c1, c2 = st.sidebar.columns(2)
        yr_def = years.index(_lfy) if _lfy in years else 0
        yr = c1.selectbox("Year", years, index=yr_def, key="cap_my")
        months = list(range(1, 13))
        mdef = _lfm if yr == _lfy else (win_end.month if yr == win_end.year else 12)
        mo = c2.selectbox(
            "Month", months, index=mdef - 1,
            format_func=lambda m: dt.date(2000, m, 1).strftime("%b"),
            key="cap_mm",
        )
        start_d, end_d = dt.date(yr, mo, 1), _eom(yr, mo)
    elif mode == "Quarter":
        c1, c2 = st.sidebar.columns(2)
        yr = c1.selectbox("Year", years, index=0, key="cap_qy")
        q = c2.selectbox("Quarter", ["Q1", "Q2", "Q3", "Q4"], key="cap_qq")
        sm = (int(q[1]) - 1) * 3 + 1
        start_d, end_d = dt.date(yr, sm, 1), _eom(yr, sm + 2)
    elif mode == "Year":
        yr = st.sidebar.selectbox("Year", years, index=0, key="cap_yy")
        start_d, end_d = dt.date(yr, 1, 1), dt.date(yr, 12, 31)
    else:
        c1, c2 = st.sidebar.columns(2)
        start_d = c1.date_input(
            "Start", value=win_start, min_value=win_start, max_value=win_end,
            key="cap_cs",
        )
        end_d = c2.date_input(
            "End", value=win_end, min_value=win_start, max_value=win_end,
            key="cap_ce",
        )

    start_d = max(start_d, win_start)
    end_d = min(end_d, win_end)
    return start_d, end_d


# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

def _load_intervals(st, hub, analytics, a, terms, start_d, end_d, share):
    """Load 15-min generation, node prices, and hub prices for the window."""

    @st.cache_data(show_spinner="Loading generation & prices...")
    def _fetch(rnode, hub_name, start_str, end_str, terms_key):
        s = pd.Timestamp(start_str)
        e = pd.Timestamp(end_str) + pd.Timedelta(days=1)

        gen = hub.generation(rnode, s, e)
        if gen is None or (hasattr(gen, "empty") and gen.empty):
            gen = pd.DataFrame()
        else:
            gen = gen.copy()

        hp = hub.hub_prices(hub_name, s, e)
        if hp is None or (hasattr(hp, "empty") and hp.empty):
            hp = pd.DataFrame()
        else:
            hp = hp.copy()

        np_ = hub.node_prices(rnode, s, e)
        if np_ is None or (hasattr(np_, "empty") and np_.empty):
            np_ = pd.DataFrame()
        else:
            np_ = np_.copy()

        return gen, hp, np_

    rnode = a["resource_node"]
    hub_name = a.get("hub", rnode)
    gen_df, hub_df, node_df = _fetch(
        rnode, hub_name,
        str(start_d), str(end_d),
        tuple(sorted(terms.items())),
    )
    return gen_df, hub_df, node_df


def _build_merged(gen_df, hub_df, node_df, share):
    """Merge generation with hub & node prices into a single 15-min frame."""
    if gen_df.empty:
        return pd.DataFrame()

    df = gen_df.copy()
    df["interval_start"] = pd.to_datetime(df["interval_start"])
    if "mwh" not in df.columns:
        df["mwh"] = df["mw"] * 0.25
    df["mwh"] = df["mwh"] * share

    # Aggregate multiple units at the same interval
    df = df.groupby("interval_start", as_index=False).agg({"mwh": "sum"})

    # Merge hub prices
    if not hub_df.empty:
        hp = hub_df.copy()
        hp["interval_start"] = pd.to_datetime(hp["interval_start"])
        hpc = _price_col(hp)
        hp = hp[["interval_start", hpc]].rename(columns={hpc: "hub_price"})
        hp = hp.drop_duplicates("interval_start")
        df = df.merge(hp, on="interval_start", how="left")
    else:
        df["hub_price"] = np.nan

    # Merge node prices
    if not node_df.empty:
        np_ = node_df.copy()
        np_["interval_start"] = pd.to_datetime(np_["interval_start"])
        npc = _price_col(np_)
        np_ = np_[["interval_start", npc]].rename(columns={npc: "node_price"})
        np_ = np_.drop_duplicates("interval_start")
        df = df.merge(np_, on="interval_start", how="left")
    else:
        df["node_price"] = np.nan

    df["hour"] = df["interval_start"].dt.hour
    df["month"] = df["interval_start"].dt.month
    df["month_label"] = df["interval_start"].dt.to_period("M").astype(str)
    df["season"] = df["month"].map(_season)

    return df


def _add_settle_price(df: pd.DataFrame, settles_at_node: bool) -> pd.DataFrame:
    """Add the ``settle_price`` column (node if node-settled, else hub)."""
    if df.empty:
        return df
    if settles_at_node and df["node_price"].notna().any():
        df["settle_price"] = df["node_price"]
    else:
        df["settle_price"] = df["hub_price"]
    return df


# ---------------------------------------------------------------------------
# Render
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
    """Render the Capture Rate Analysis page."""

    share = float(terms.get("volume_share_pct", 100.0)) / 100.0
    tech = str(a.get("tech", "")).lower()
    rnode = a["resource_node"]
    hub_name = a.get("hub", rnode)

    branding.hero(st, "Capture Rate Analysis",
                  f"How well does this {tech or 'plant'} capture market prices?")

    win_start_d = win_start if isinstance(win_start, dt.date) else pd.Timestamp(win_start).date()
    win_end_d = win_end if isinstance(win_end, dt.date) else pd.Timestamp(win_end).date()

    start_d, end_d = _period_picker(st, win_start_d, win_end_d)

    if start_d > end_d:
        st.error(f"Selected period ({start_d} -> {end_d}) is outside the "
                 f"settled window ({win_start_d} -> {win_end_d}).")
        branding.footer(st)
        return

    st.caption(f"Analysing **{start_d} -> {end_d}** | "
               f"node **{rnode}** | hub **{hub_name}**")

    # ── load data ────────────────────────────────────────────────────────────
    gen_df, hub_df, node_df = _load_intervals(
        st, hub, analytics, a, terms, start_d, end_d, share,
    )

    df = _build_merged(gen_df, hub_df, node_df, share)

    if df.empty or df["mwh"].sum() == 0:
        st.info("No generation data available for this period.")
        branding.footer(st)
        return

    # ── settle location (node vs hub) ────────────────────────────────────────
    settle_loc = contract.settle_location(terms)
    settles_at_node = not str(settle_loc).upper().startswith("HB_")

    # The "price" column the plant settles against
    df = _add_settle_price(df, settles_at_node)

    # Full-window frame for the inherently multi-month views (monthly trend +
    # hour×month heatmap). These need every month regardless of the period
    # picker, otherwise a "Month" selection collapses the heatmap to one row.
    if start_d <= win_start_d and end_d >= win_end_d:
        df_full = df  # period already spans the whole window
    else:
        gen_f, hub_f, node_f = _load_intervals(
            st, hub, analytics, a, terms, win_start_d, win_end_d, share,
        )
        df_full = _add_settle_price(
            _build_merged(gen_f, hub_f, node_f, share), settles_at_node,
        )
    if df_full.empty:
        df_full = df

    # ======================================================================
    # 1. KPI row
    # ======================================================================
    st.subheader("Capture Summary")

    mask_gen = df["mwh"] > 0

    total_mwh = float(df["mwh"].sum())
    capture_price = (
        float((df.loc[mask_gen, "mwh"] * df.loc[mask_gen, "settle_price"]).sum()
              / df.loc[mask_gen, "mwh"].sum())
        if mask_gen.any() and df.loc[mask_gen, "mwh"].sum() > 0
        else float("nan")
    )

    # Mean grid price: time-weighted average hub price over the full period
    hub_mask = df["hub_price"].notna()
    mean_grid_price = float(df.loc[hub_mask, "hub_price"].mean()) if hub_mask.any() else float("nan")

    capture_ratio = (
        100.0 * capture_price / mean_grid_price
        if pd.notna(capture_price) and pd.notna(mean_grid_price) and mean_grid_price != 0
        else float("nan")
    )

    # Basis spread: gen-weighted node price minus time-weighted hub average
    if settles_at_node and df["node_price"].notna().any() and mask_gen.any():
        node_capture = float(
            (df.loc[mask_gen, "mwh"] * df.loc[mask_gen, "node_price"]).sum()
            / df.loc[mask_gen, "mwh"].sum()
        )
        basis_spread = node_capture - mean_grid_price if pd.notna(mean_grid_price) else float("nan")
    else:
        basis_spread = float("nan")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Capture price",
        f"${capture_price:,.2f}/MWh" if pd.notna(capture_price) else "---",
        delta="generation-weighted",
        delta_color="off",
        help="Generation-weighted average market price: "
             "sum(MWh x price) / sum(MWh).",
    )
    c2.metric(
        "Mean grid price",
        f"${mean_grid_price:,.2f}/MWh" if pd.notna(mean_grid_price) else "---",
        delta=f"time-weighted hub avg ({hub_name})",
        delta_color="off",
        help="Simple time-weighted average hub price over the selected period.",
    )
    c3.metric(
        "Capture ratio",
        f"{capture_ratio:.1f}%" if pd.notna(capture_ratio) else "---",
        delta=(f"{'above' if capture_ratio >= 100 else 'below'} grid average"
               if pd.notna(capture_ratio) else ""),
        delta_color="normal" if pd.notna(capture_ratio) else "off",
        help="Capture price / mean grid price x 100.  "
             "Below 100% means the plant generates disproportionately "
             "during lower-priced hours.",
    )
    c4.metric(
        "Basis spread",
        f"${basis_spread:+,.2f}/MWh" if pd.notna(basis_spread) else "N/A",
        delta="node capture minus hub avg" if pd.notna(basis_spread) else "hub-settled",
        delta_color="off",
        help="Generation-weighted node capture price minus the time-weighted "
             "hub average. Shows the locational basis the plant earns or gives up.",
    )

    st.divider()

    # ======================================================================
    # 2. Monthly capture ratio trend chart
    # ======================================================================
    st.subheader("Monthly Capture Trend")
    st.caption("Full settled history — independent of the period selected above.")

    full_gen_mask = df_full["mwh"] > 0
    full_hub_mask = df_full["hub_price"].notna()

    monthly = df_full.loc[full_gen_mask].groupby("month_label").apply(
        lambda g: pd.Series({
            "capture_price": float((g["mwh"] * g["settle_price"]).sum() / g["mwh"].sum())
            if g["mwh"].sum() > 0 else float("nan"),
            "mwh": g["mwh"].sum(),
        }),
        include_groups=False,
    ).reset_index()

    # Hub monthly average
    hub_monthly = df_full.loc[full_hub_mask].groupby("month_label")["hub_price"].mean().reset_index()
    hub_monthly.columns = ["month_label", "hub_avg"]
    monthly = monthly.merge(hub_monthly, on="month_label", how="left")
    monthly["capture_ratio"] = 100.0 * monthly["capture_price"] / monthly["hub_avg"]
    monthly = monthly.sort_values("month_label").reset_index(drop=True)

    if not monthly.empty:
        bar_colors = [
            branding.GOOD if cp >= ha else branding.BAD
            for cp, ha in zip(monthly["capture_price"], monthly["hub_avg"])
        ]

        from plotly.subplots import make_subplots

        fig_mo = make_subplots(specs=[[{"secondary_y": True}]])

        fig_mo.add_bar(
            x=monthly["month_label"],
            y=monthly["capture_price"],
            marker_color=bar_colors,
            name="Capture price ($/MWh)",
            hovertemplate="%{x}<br>Capture: $%{y:,.2f}/MWh<extra></extra>",
        )

        fig_mo.add_scatter(
            x=monthly["month_label"],
            y=monthly["capture_ratio"],
            mode="lines+markers",
            line=dict(color=branding.ACCENT, width=2.5),
            marker=dict(size=6, color=branding.ACCENT),
            name="Capture ratio (%)",
            hovertemplate="%{x}<br>Ratio: %{y:.1f}%<extra></extra>",
            secondary_y=True,
        )

        # 100% reference line
        fig_mo.add_hline(
            y=100, line_dash="dash", line_color="rgba(120,120,120,0.5)",
            annotation_text="100%", annotation_position="top left",
            secondary_y=True,
        )

        fig_mo.update_layout(
            height=400,
            hovermode="x unified",
            margin=dict(t=30, b=10),
            legend=dict(orientation="h", y=1.12),
            bargap=0.2,
        )
        fig_mo.update_yaxes(title_text="Capture price ($/MWh)", secondary_y=False)
        fig_mo.update_yaxes(
            title_text="Capture ratio (%)",
            showgrid=False,
            secondary_y=True,
            tickfont=dict(color=branding.ACCENT),
            title_font=dict(color=branding.ACCENT),
        )
        st.plotly_chart(fig_mo, use_container_width=True)

    st.divider()

    # ======================================================================
    # 3. Hourly price-generation correlation by season
    # ======================================================================
    st.subheader("Hourly Price-Generation Profile")

    seasons = sorted(df["season"].unique(), key=lambda s: ["Summer", "Shoulder", "Winter"].index(s)
                     if s in ("Summer", "Shoulder", "Winter") else 3)
    if seasons:
        sel_season = st.radio("Season", seasons, horizontal=True, key="cap_season")

        sdf = df[df["season"] == sel_season].copy()

        hourly_gen = sdf.groupby("hour")["mwh"].mean().reindex(range(24), fill_value=0)
        hourly_price = sdf.groupby("hour")["hub_price"].mean().reindex(range(24))

        from plotly.subplots import make_subplots as _ms

        fig_hr = _ms(specs=[[{"secondary_y": True}]])

        fig_hr.add_scatter(
            x=list(range(24)),
            y=hourly_gen.values,
            fill="tozeroy",
            fillcolor="rgba(136,169,24,0.25)",
            line=dict(color=branding.GOOD, width=2),
            name="Avg generation (MWh)",
            hovertemplate="Hour %{x}<br>Gen: %{y:,.2f} MWh<extra></extra>",
        )

        fig_hr.add_scatter(
            x=list(range(24)),
            y=hourly_price.values,
            mode="lines+markers",
            line=dict(color="rgba(218,165,32,0.85)", width=2.5),
            marker=dict(size=5, color="rgba(218,165,32,0.85)"),
            name="Avg hub price ($/MWh)",
            hovertemplate="Hour %{x}<br>Price: $%{y:,.2f}/MWh<extra></extra>",
            secondary_y=True,
        )

        fig_hr.update_layout(
            height=380,
            hovermode="x unified",
            margin=dict(t=30, b=10),
            legend=dict(orientation="h", y=1.12),
            xaxis=dict(
                title="Hour of day (CPT)",
                tickmode="linear",
                dtick=1,
            ),
        )
        fig_hr.update_yaxes(title_text="Avg generation (MWh)", rangemode="tozero",
                            secondary_y=False)
        fig_hr.update_yaxes(
            title_text="Avg hub price ($/MWh)",
            showgrid=False,
            secondary_y=True,
            tickfont=dict(color="rgba(218,165,32,0.85)"),
            title_font=dict(color="rgba(218,165,32,0.85)"),
        )
        st.plotly_chart(fig_hr, use_container_width=True)

        # Descriptive caption
        if "solar" in tech:
            st.caption(
                "Solar generation peaks at midday.  When hub prices dip during "
                "those hours (the 'duck curve'), the capture ratio falls below 100%."
            )
        elif "wind" in tech:
            st.caption(
                "Wind generation tends to peak overnight and in early morning when "
                "hub prices are typically lower, pulling the capture ratio below 100%."
            )

    st.divider()

    # ======================================================================
    # 4. Capture heatmap (hour x month)
    # ======================================================================
    st.subheader("Capture Heatmap")
    st.caption("Average capture ratio by hour of day and month over the full "
               "settled history.  Green = capturing above that month's grid "
               "average; grey ≈ 100%; red = below.")

    # Build pivot: rows=calendar month (YYYY-MM), cols=hour, values=capture ratio
    hm = df_full.loc[full_gen_mask].copy()
    if len(hm) > 0 and full_hub_mask.any():
        # Hub average per calendar month for the denominator
        hub_mo_avg = df_full.loc[full_hub_mask].groupby("month_label")["hub_price"].mean()

        # Gen-weighted capture per hour × month bucket
        hm["mwh_x_price"] = hm["mwh"] * hm["settle_price"]
        piv_num = hm.pivot_table(values="mwh_x_price", index="month_label",
                                 columns="hour", aggfunc="sum")
        piv_den = hm.pivot_table(values="mwh", index="month_label",
                                 columns="hour", aggfunc="sum")
        piv_cap = piv_num / piv_den  # capture price per bucket

        # Divide by that month's hub average to get capture ratio (%)
        piv_ratio = piv_cap.copy()
        for mo in piv_ratio.index:
            avg = hub_mo_avg.get(mo, np.nan)
            piv_ratio.loc[mo] = (100.0 * piv_ratio.loc[mo] / avg
                                 if pd.notna(avg) and avg != 0 else np.nan)

        # Full 0-23 columns, chronological rows
        piv_ratio = piv_ratio.reindex(columns=range(24)).sort_index()

        n_rows = len(piv_ratio)
        fig_hm = go.Figure(data=go.Heatmap(
            z=piv_ratio.values,
            x=[str(h) for h in range(24)],
            y=list(piv_ratio.index),
            colorscale=[
                [0.0, branding.BAD],
                [0.5, "#888888"],
                [1.0, branding.GOOD],
            ],
            zmid=100,
            xgap=1, ygap=1,
            colorbar=dict(title="Capture %"),
            hovertemplate="Hour %{x}, %{y}<br>Capture: %{z:.1f}%<extra></extra>",
        ))

        fig_hm.update_layout(
            height=min(900, max(260, 30 * n_rows + 120)),
            margin=dict(t=20, b=10),
            xaxis=dict(title="Hour of day (CPT)", dtick=1),
            yaxis=dict(title="Month", autorange="reversed", type="category"),
        )
        st.plotly_chart(fig_hm, use_container_width=True)
    else:
        st.info("Insufficient data to build the capture heatmap.")

    st.divider()

    # ======================================================================
    # 5. Price-generation scatter
    # ======================================================================
    st.subheader("Price vs. Generation Scatter")
    st.caption("Each dot is a 15-minute interval.  Colour encodes hour of day.")

    scat = df.loc[mask_gen & df["settle_price"].notna()].copy()
    if not scat.empty:
        fig_sc = go.Figure(data=go.Scattergl(
            x=scat["mwh"],
            y=scat["settle_price"],
            mode="markers",
            marker=dict(
                size=3,
                color=scat["hour"],
                colorscale="Viridis",
                colorbar=dict(title="Hour"),
                opacity=0.5,
            ),
            hovertemplate=(
                "Gen: %{x:.2f} MWh<br>"
                "Price: $%{y:.2f}/MWh<br>"
                "<extra></extra>"
            ),
        ))

        fig_sc.update_layout(
            height=420,
            margin=dict(t=20, b=10),
            xaxis=dict(title="Generation (MWh per 15-min interval)"),
            yaxis=dict(title="Settlement price ($/MWh)"),
        )
        st.plotly_chart(fig_sc, use_container_width=True)
    else:
        st.info("No intervals with both generation and price data for the scatter plot.")

    st.divider()

    # ======================================================================
    # Download / export
    # ======================================================================
    with st.expander("Download data"):
        export_df = df[["interval_start", "mwh", "hub_price", "node_price",
                        "settle_price", "hour", "month_label"]].copy()
        export_df.columns = [
            "Interval", "MWh", "Hub_price_$/MWh", "Node_price_$/MWh",
            "Settle_price_$/MWh", "Hour", "Month",
        ]

        # Try the shared download_block helper first
        try:
            from app._export import download_block
            download_block(
                st, export_df,
                name=f"capture_analysis_{a.get('resource_node', 'asset')}",
                title="Capture Rate Analysis",
                meta={
                    "Asset": str(a.get("project_name", a.get("resource_node", ""))),
                    "Resource node": rnode,
                    "Hub": hub_name,
                    "Period": f"{start_d} -> {end_d}",
                    "Capture price ($/MWh)": f"{capture_price:,.2f}" if pd.notna(capture_price) else "---",
                    "Mean grid price ($/MWh)": f"{mean_grid_price:,.2f}" if pd.notna(mean_grid_price) else "---",
                    "Capture ratio (%)": f"{capture_ratio:.1f}" if pd.notna(capture_ratio) else "---",
                },
                key="cap_export",
            )
        except (ImportError, Exception):
            # Fallback: plain CSV
            csv = export_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download CSV",
                data=csv,
                file_name=f"capture_analysis_{rnode}_{start_d}_{end_d}.csv",
                mime="text/csv",
                key="cap_csv_dl",
            )

    branding.footer(st)
