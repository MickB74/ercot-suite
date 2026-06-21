"""Shared look-and-feel for the customer-facing pages.

One header, one palette, a couple of small render helpers. Keeping it here means
every page reads the same and a rebrand is a one-file change.
"""

from __future__ import annotations

from . import contract

# ── SR Inc. brand palette (from the official /sr-deck theme) ─────────────────
SR_BLUE = "#0069B3"        # primary — headers, rules, panel fills
SR_BLUE_DARK = "#00558E"   # darker tint, gradients/hover
SR_BLUE_LIGHT = "#54A4DA"  # secondary blue
SR_BLUE_PALE = "#D7E2F2"   # light backgrounds / borders
SR_BLUE_GHOST = "#ECF0F9"  # very light panel fill
SR_GREEN = "#88A918"       # accent — stats, eyebrow labels, callouts
SR_GREEN_ALT = "#01A06F"   # secondary green accent
SR_DARK_GREY = "#63666F"   # body text
SR_MID_GREY = "#848484"    # subtitles / captions
SR_NAVY = "#001F5F"        # deep navy

# Roles used across the app (kept as the old names so pages don't change):
PRIMARY = SR_BLUE          # dominant brand colour
ACCENT = SR_GREEN          # accent — the SR green
GOOD = SR_GREEN            # offtaker receives (brand green)
BAD = "#B23A48"            # offtaker pays — muted brand-harmonised red (data semaphore)

# Title font stack: Avenir Next (per the deck) → graceful web fallbacks; body and
# everything else is Open Sans (the SR body face), loaded from Google Fonts.
_TITLE_FONT = '"Avenir Next", "Avenir", "Segoe UI", "Open Sans", Calibri, sans-serif'

_CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap');
  html, body, [class*="css"], .stMarkdown, p, li, span, label {{
    font-family: 'Open Sans', Calibri, sans-serif;
  }}
  h1, h2, h3, h4 {{ font-family: {_TITLE_FONT}; color: {SR_BLUE}; }}

  /* Hero — SR Blue panel with a green eyebrow, mirroring the deck title slide. */
  .portal-hero {{
    background: linear-gradient(105deg, {SR_BLUE} 0%, {SR_BLUE_DARK} 100%);
    color: #fff; padding: 1.15rem 1.4rem 1.25rem; border-radius: 10px;
    margin-bottom: 1rem; box-shadow: 0 6px 22px rgba(0,105,179,0.20);
  }}
  .portal-hero .eyebrow {{
    color: #cfe6b0; font-size: .72rem; font-weight: 700; letter-spacing: 1.5px;
    text-transform: uppercase; margin-bottom: .3rem;
  }}
  .portal-hero h1 {{
    margin: 0; font-size: 1.6rem; color: #fff !important;
    font-family: {_TITLE_FONT}; letter-spacing: .2px;
  }}
  .portal-hero .sub {{ opacity: .94; font-size: .95rem; margin-top: .3rem; }}
  /* SR signature: thin rule under the title block. */
  .portal-hero .rule {{
    height: 3px; width: 64px; background: {SR_GREEN}; border-radius: 2px;
    margin: .7rem 0 .55rem;
  }}
  .portal-hero .chips {{ margin-top: .15rem; }}
  .portal-chip {{
    display: inline-block; background: rgba(255,255,255,.15); border-radius: 999px;
    padding: .14rem .72rem; margin: .15rem .35rem .15rem 0; font-size: .8rem;
  }}

  /* Eyebrow label helper (SR green caps). */
  .portal-eyebrow {{
    color: {SR_GREEN}; font-size: .78rem; font-weight: 700; letter-spacing: 1.4px;
    text-transform: uppercase; margin: .2rem 0 .35rem;
  }}

  /* KPI cards — SR ghost fill, blue-pale border, green letter-spaced labels. */
  div[data-testid="stMetric"] {{
    background: {SR_BLUE_GHOST}; border: 1px solid {SR_BLUE_PALE};
    border-radius: 10px; padding: .75rem .95rem;
  }}
  div[data-testid="stMetricLabel"] p {{
    color: {SR_GREEN}; font-weight: 700; font-size: .72rem; letter-spacing: .8px;
    text-transform: uppercase;
  }}
  /* Smaller value so longer figures (e.g. "$15.10/MWh") fit one line. */
  div[data-testid="stMetricValue"] {{
    color: {SR_BLUE}; font-weight: 700; font-size: 1.55rem; line-height: 1.2;
  }}
  div[data-testid="stMetricValue"] > div {{ font-size: 1.55rem; }}

  .portal-foot {{
    color: {SR_MID_GREY}; font-size: .78rem; margin-top: 2rem;
    border-top: 1px solid {SR_BLUE_PALE}; padding-top: .6rem;
  }}
  .portal-foot b {{ color: {SR_DARK_GREY}; }}
</style>
"""


def inject_css(st) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def eyebrow(st, text: str) -> None:
    """Render an SR-style eyebrow label (green, ALL CAPS, letter-spaced)."""
    st.markdown(f'<div class="portal-eyebrow">{text}</div>', unsafe_allow_html=True)


def hero(st, title: str, subtitle: str = "") -> None:
    """Render the SR-branded page header with the asset's headline facts as chips."""
    inject_css(st)
    a = contract.ASSET
    _is_wind = "wind" in str(a.get("tech", "")).lower()
    if _is_wind:  # wind has no tracking/DC-AC; show turbine model if known
        _tm = a.get("turbine_model")
        tech_chip = f"🌬️ {a['tech']}" + (f" · {_tm}" if _tm else "")
    else:
        _tr = str(a.get("tracking_type", "")).replace("_", "-")
        tech_chip = f"☀️ {a['tech']}" + (f" · {_tr}" if _tr else "")
    terms = contract.load_contract()
    offtaker = str(terms.get("offtaker", "") or "").strip()
    developer = str(terms.get("developer", "") or "").strip()
    chip_items = [
        f"⚡ {a['capacity_mw']:,.0f} MW",
        tech_chip,
        f"📍 {a['county']}",
        f"🔌 {a['hub'].replace('HB_', '')} hub",
        f"🏷️ Node {a['resource_node']}",
    ]
    if offtaker:
        chip_items.append(f"🏢 Offtaker: {offtaker}")
    if developer:
        chip_items.append(f"🏗️ Developer: {developer}")
    chips = "".join(f'<span class="portal-chip">{c}</span>' for c in chip_items)
    sub = f'<div class="sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="portal-hero">'
        f'<div class="eyebrow">Mesquite Star · Settlement Portal</div>'
        f'<h1>{title}</h1>{sub}<div class="rule"></div>'
        f'<div class="chips">{chips}</div></div>',
        unsafe_allow_html=True,
    )


def footer(st) -> None:
    year = pd_year()
    st.markdown(
        f'<div class="portal-foot">Mesquite Star Settlement Portal · prepared by '
        f'<b>Sustainability Roundtable, Inc.</b> · figures derived from '
        f'ERCOT-published 15-minute metered generation and settlement-point prices. '
        f'Settlement is offtaker-signed: <b>positive = you receive, negative = you '
        f'pay</b>.<br>© {year} Sustainability Roundtable, Inc. · Confidential — for use '
        f'in connection with SR Inc. services only.</div>',
        unsafe_allow_html=True,
    )


def pd_year() -> int:
    import pandas as pd  # noqa: PLC0415
    return pd.Timestamp.now().year


# NOTE: the ``$`` is escaped (``\$``) because Streamlit treats a pair of ``$…$``
# in markdown/metrics as LaTeX math. Escaping renders a literal dollar sign in
# st.markdown / st.success / st.metric. For plain-text contexts (plotly labels,
# CSV/PDF export metadata) format the number inline instead of using these.
def money(x: float) -> str:
    r"""\$1,234,567 — markdown-safe (escaped \$) for prose and metrics."""
    return f"\\${x:,.0f}"


def signed_money(x: float) -> str:
    """Signed, markdown-safe dollar amount (escaped \\$)."""
    return f"\\${x:,.0f}" if x >= 0 else f"−\\${abs(x):,.0f}"


def signed_money_raw(x: float) -> str:
    """Signed dollar amount with a literal ``$`` — for plain text (CSV/PDF/plotly)."""
    return f"${x:,.0f}" if x >= 0 else f"-${abs(x):,.0f}"
