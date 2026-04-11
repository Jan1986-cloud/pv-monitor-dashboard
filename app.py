import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, text

# -- Config --
DB_URL = os.getenv("DATABASE_URL", "")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

SYSTEM_ID = os.getenv("SYSTEM_ID", "scheepswerf")
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "10"))

st.set_page_config(
    page_title="Zonnestroom Dashboard - Scheepswerf",
    page_icon="\u26a1",
    layout="wide",
)

# -- Dark industrial CSS --
st.markdown("""<style>
    .stApp { background-color: #0e1117; }
    .kpi-card {
        background: linear-gradient(135deg, #1a1d23 0%, #22262e 100%);
        border: 1px solid #333;
        border-radius: 12px;
        padding: 24px;
        text-align: center;
        margin-bottom: 16px;
    }
    .kpi-card .label {
        font-size: 14px;
        color: #8892a0;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 8px;
    }
    .kpi-card .value {
        font-size: 42px;
        font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
    }
    .kpi-card .sub {
        font-size: 13px;
        margin-top: 4px;
    }
    .green { color: #00e676; }
    .red { color: #ff5252; }
    .orange { color: #ffab40; }
    .cyan { color: #00bcd4; }
    .dim { color: #5c6370; }
    .inv-card {
        background: #1a1d23;
        border: 1px solid #2a2e36;
        border-radius: 10px;
        padding: 20px;
    }
    .inv-card h3 {
        margin: 0 0 14px 0;
        color: #b0bec5;
        font-size: 18px;
        border-bottom: 1px solid #333;
        padding-bottom: 10px;
    }
    .inv-row {
        display: flex;
        justify-content: space-between;
        padding: 6px 0;
        border-bottom: 1px solid #1e2229;
    }
    .inv-row .lbl { color: #6b7280; font-size: 14px; }
    .inv-row .val { color: #e0e0e0; font-weight: 600; font-size: 14px; }
    h1 { color: #e0e0e0 !important; }
    .timestamp-bar {
        text-align: right;
        color: #5c6370;
        font-size: 12px;
        padding: 4px 0 12px 0;
    }
    #MainMenu, footer, header { visibility: hidden; }
</style>""", unsafe_allow_html=True)

# -- Database helper --
@st.cache_resource
def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True, pool_size=5)

def load_latest(engine):
    q = text("""
        SELECT timestamp, p1_grid_w, total_limit_w,
               inv_40k_limit_w, inv_40k_actual_w, inv_40k_pv_v,
               inv_50k_limit_w, inv_50k_actual_w, inv_50k_pv_v,
               (inv_40k_actual_w + inv_50k_actual_w) AS inv_total_w
        FROM telemetry_data
        WHERE system_id = :sid
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(q, {"sid": SYSTEM_ID}).mappings().first()
    return dict(row) if row else None

def load_history(engine, hours=24):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = text("""
        SELECT timestamp, p1_grid_w,
               inv_40k_actual_w, inv_50k_actual_w,
               (inv_40k_actual_w + inv_50k_actual_w) AS inv_total_w
        FROM telemetry_data
        WHERE system_id = :sid AND timestamp >= :cutoff
        ORDER BY timestamp ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"sid": SYSTEM_ID, "cutoff": cutoff})
    return df

# -- Main dashboard --
st.title("\u26a1 Dashboard Zonnestroom & Netaansluiting \u2014 Scheepswerf")

engine = get_engine()
latest = load_latest(engine)

if latest is None:
    st.warning("Geen telemetrie-data beschikbaar. Wacht op data van het ESP32-systeem.")
    st.stop()

ts = latest["timestamp"]
ts_str = ts.strftime("%d-%m-%Y %H:%M:%S UTC") if hasattr(ts, "strftime") else str(ts)
st.markdown(f'<div class="timestamp-bar">Laatste meting: {ts_str}</div>', unsafe_allow_html=True)

# -- KPI Cards --
grid_w = latest["p1_grid_w"] or 0
solar_w = (latest["inv_40k_actual_w"] or 0) + (latest["inv_50k_actual_w"] or 0)
total_limit = latest["total_limit_w"] or 0

if grid_w < 0:
    grid_color = "green"
    grid_label = "(Teruglevering)"
elif grid_w == 0:
    grid_color = "dim"
    grid_label = "(Neutraal)"
else:
    grid_color = "red"
    grid_label = "(Afname)"

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Actueel Netverbruik</div>
        <div class="value {grid_color}">{grid_w:,} W</div>
        <div class="sub {grid_color}">{grid_label}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Actuele Zonnestroom</div>
        <div class="value green">{solar_w:,} W</div>
        <div class="sub dim">Solis 40K + Solis 50K</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Totaal Limiet</div>
        <div class="value cyan">{total_limit:,} W</div>
        <div class="sub dim">Netbeheerder begrenzing</div>
    </div>
    """, unsafe_allow_html=True)

# -- History chart --
st.markdown("---")
hours = st.selectbox("Periode", [6, 12, 24, 48, 72], index=2,
                     format_func=lambda h: f"Laatste {h} uur")

df = load_history(engine, hours=hours)

if df.empty:
    st.info("Geen historische data voor de geselecteerde periode.")
else:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["p1_grid_w"],
        name="Net (W)",
        line=dict(color="#ff5252", width=2),
        fill="tozeroy", fillcolor="rgba(255,82,82,0.08)",
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["inv_total_w"],
        name="Zonnestroom Totaal (W)",
        line=dict(color="#00e676", width=2),
        fill="tozeroy", fillcolor="rgba(0,230,118,0.08)",
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#13161c",
        title=dict(text="Historisch Overzicht", font=dict(size=18, color="#b0bec5")),
        xaxis=dict(title="Tijd", gridcolor="#1e2229"),
        yaxis=dict(title="Vermogen (W)", gridcolor="#1e2229"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

# -- Inverter details --
st.markdown("---")
st.subheader("Omvormer Details")
inv_col1, inv_col2 = st.columns(2)

inv40_w = latest["inv_40k_actual_w"] or 0
inv50_w = latest["inv_50k_actual_w"] or 0
inv40_limit = latest["inv_40k_limit_w"] or 0
inv50_limit = latest["inv_50k_limit_w"] or 0
inv40_pv_v = latest["inv_40k_pv_v"] or 0.0
inv50_pv_v = latest["inv_50k_pv_v"] or 0.0

with inv_col1:
    pct40 = (inv40_w / solar_w * 100) if solar_w else 0
    st.markdown(f"""
    <div class="inv-card">
        <h3>Solis 40K</h3>
        <div class="inv-row"><span class="lbl">Actueel Vermogen</span><span class="val green">{inv40_w:,} W</span></div>
        <div class="inv-row"><span class="lbl">Limiet</span><span class="val cyan">{inv40_limit:,} W</span></div>
        <div class="inv-row"><span class="lbl">PV Spanning</span><span class="val">{inv40_pv_v:.1f} V</span></div>
        <div class="inv-row"><span class="lbl">Aandeel Totaal</span><span class="val">{pct40:.0f}%</span></div>
    </div>
    """, unsafe_allow_html=True)

with inv_col2:
    pct50 = (inv50_w / solar_w * 100) if solar_w else 0
    st.markdown(f"""
    <div class="inv-card">
        <h3>Solis 50K</h3>
        <div class="inv-row"><span class="lbl">Actueel Vermogen</span><span class="val green">{inv50_w:,} W</span></div>
        <div class="inv-row"><span class="lbl">Limiet</span><span class="val cyan">{inv50_limit:,} W</span></div>
        <div class="inv-row"><span class="lbl">PV Spanning</span><span class="val">{inv50_pv_v:.1f} V</span></div>
        <div class="inv-row"><span class="lbl">Aandeel Totaal</span><span class="val">{pct50:.0f}%</span></div>
    </div>
    """, unsafe_allow_html=True)

# -- Auto-refresh --
st.markdown(f"""<meta http-equiv="refresh" content="{REFRESH_SECONDS}">""", unsafe_allow_html=True)
st.markdown(f'<div class="timestamp-bar">Auto-verversing elke {REFRESH_SECONDS} seconden</div>',
            unsafe_allow_html=True)
