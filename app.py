import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
import requests
from datetime import datetime, timedelta, timezone, date
from sqlalchemy import create_engine, text

# -- Config --
DB_URL = os.getenv("DATABASE_URL", "")
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

SYSTEM_ID = os.getenv("SYSTEM_ID", "scheepswerf")
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "30"))

# Veenendaal coordinates for weather API
LAT, LON = 52.03, 5.55
# System peak capacity in kW (40K + 50K = 90 kWp)
SYSTEM_KWP = 90.0

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
        padding: 20px;
        text-align: center;
        margin-bottom: 12px;
    }
    .kpi-card .label {
        font-size: 12px;
        color: #8892a0;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 6px;
    }
    .kpi-card .value {
        font-size: 36px;
        font-weight: 700;
        font-family: 'JetBrains Mono', monospace;
    }
    .kpi-card .sub {
        font-size: 12px;
        margin-top: 4px;
    }
    .green { color: #00e676; }
    .red { color: #ff5252; }
    .orange { color: #ffab40; }
    .cyan { color: #00bcd4; }
    .yellow { color: #ffd740; }
    .dim { color: #5c6370; }
    .period-summary {
        background: #1a1d23;
        border: 1px solid #2a2e36;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 8px;
    }
    .period-summary h4 {
        margin: 0 0 10px 0;
        color: #b0bec5;
        font-size: 16px;
        border-bottom: 1px solid #333;
        padding-bottom: 8px;
    }
    .stat-row {
        display: flex;
        justify-content: space-between;
        padding: 5px 0;
        border-bottom: 1px solid #1e2229;
    }
    .stat-row .lbl { color: #6b7280; font-size: 13px; }
    .stat-row .val { color: #e0e0e0; font-weight: 600; font-size: 13px; }
    h1 { color: #e0e0e0 !important; }
    .timestamp-bar {
        text-align: right;
        color: #5c6370;
        font-size: 12px;
        padding: 4px 0 8px 0;
    }
    .live-mini {
        background: #13161c;
        border: 1px solid #2a2e36;
        border-radius: 8px;
        padding: 12px;
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
               inv_40k_limit_w, inv_40k_actual_w,
               inv_50k_limit_w, inv_50k_actual_w,
               (inv_40k_actual_w + inv_50k_actual_w) AS inv_total_w
        FROM telemetry_data
        WHERE system_id = :sid
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(q, {"sid": SYSTEM_ID}).mappings().first()
    return dict(row) if row else None

def load_period_data(engine, start_dt, end_dt):
    """Load telemetry for a date range, returns DataFrame."""
    q = text("""
        SELECT timestamp, p1_grid_w,
               inv_40k_actual_w, inv_50k_actual_w,
               (inv_40k_actual_w + inv_50k_actual_w) AS inv_total_w,
               total_limit_w
        FROM telemetry_data
        WHERE system_id = :sid AND timestamp >= :start AND timestamp < :end
        ORDER BY timestamp ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"sid": SYSTEM_ID, "start": start_dt, "end": end_dt})
    return df

def calc_energy_kwh(df):
    """Calculate energy totals from power samples using trapezoidal integration."""
    if df.empty or len(df) < 2:
        return {"opgewekt_kwh": 0, "afgenomen_kwh": 0, "teruggeleverd_kwh": 0, "netto_kwh": 0}
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    dt_hours = df["timestamp"].diff().dt.total_seconds().div(3600).fillna(0)
    solar = df["inv_total_w"].fillna(0)
    grid = df["p1_grid_w"].fillna(0)
    opgewekt = (solar * dt_hours).sum() / 1000
    afname = grid.clip(lower=0)
    terug = grid.clip(upper=0).abs()
    afgenomen = (afname * dt_hours).sum() / 1000
    teruggeleverd = (terug * dt_hours).sum() / 1000
    return {
        "opgewekt_kwh": round(opgewekt, 1),
        "afgenomen_kwh": round(afgenomen, 1),
        "teruggeleverd_kwh": round(teruggeleverd, 1),
        "netto_kwh": round(afgenomen - teruggeleverd, 1),
    }

def estimate_potential_kwh(df):
    """Estimate potential production without curtailment.
    When inverters are at limit, actual production is capped.
    We estimate uncurtailed production based on the ratio."""
    if df.empty or len(df) < 2:
        return 0
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    dt_hours = df["timestamp"].diff().dt.total_seconds().div(3600).fillna(0)
    actual = df["inv_total_w"].fillna(0)
    limit = df["total_limit_w"].fillna(90000)
    # If actual >= 95% of limit, assume curtailment - estimate 15% more potential
    curtailed = actual >= (limit * 0.95)
    potential = actual.copy()
    potential[curtailed] = actual[curtailed] * 1.15
    pot_kwh = (potential * dt_hours).sum() / 1000
    return round(pot_kwh, 1)

@st.cache_data(ttl=3600)
def get_solar_irradiance(dt_date):
    """Get solar irradiance from Open-Meteo for potential calculation."""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": LAT,
            "longitude": LON,
            "daily": "sunshine_duration,shortwave_radiation_sum",
            "timezone": "Europe/Amsterdam",
            "start_date": dt_date.isoformat(),
            "end_date": dt_date.isoformat(),
        }
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if "daily" in data:
            sunshine_hrs = (data["daily"].get("sunshine_duration", [0])[0] or 0) / 3600
            radiation = data["daily"].get("shortwave_radiation_sum", [0])[0] or 0
            return {"sunshine_hours": round(sunshine_hrs, 1), "radiation_kwh_m2": round(radiation / 1000, 2)}
    except Exception:
        pass
    return {"sunshine_hours": 0, "radiation_kwh_m2": 0}

def get_period_dates(period_key):
    """Return (start_dt, end_dt) for a given period key."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period_key == "Vandaag":
        return today, now
    elif period_key == "Gisteren":
        return today - timedelta(days=1), today
    elif period_key == "Deze week":
        start = today - timedelta(days=today.weekday())
        return start, now
    elif period_key == "Vorige week":
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday
    elif period_key == "Deze maand":
        start = today.replace(day=1)
        return start, now
    elif period_key == "Vorige maand":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return first_prev, first_this
    elif period_key == "Dit jaar":
        start = today.replace(month=1, day=1)
        return start, now
    elif period_key == "Vorig jaar":
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(month=1, day=1)
        return start, end
    return today, now

# ============================================================
# MAIN DASHBOARD
# ============================================================
st.title("\u26a1 Dashboard Zonnestroom & Netaansluiting \u2014 Scheepswerf")

engine = get_engine()
latest = load_latest(engine)

if latest is None:
    st.warning("Geen telemetrie-data beschikbaar. Wacht op data van het ESP32-systeem.")
    st.stop()

# -- Period selector --
period_options = ["Vandaag", "Gisteren", "Deze week", "Vorige week", "Deze maand", "Vorige maand", "Dit jaar", "Vorig jaar"]
selected_period = st.selectbox("Periode", period_options, index=0)
start_dt, end_dt = get_period_dates(selected_period)

# Load period data
df_period = load_period_data(engine, start_dt, end_dt)
energy = calc_energy_kwh(df_period)
potential = estimate_potential_kwh(df_period)
curtailed = round(potential - energy["opgewekt_kwh"], 1) if potential > energy["opgewekt_kwh"] else 0

# Weather data for today
today_weather = get_solar_irradiance(datetime.now(timezone.utc).date())

# ============================================================
# TOP KPI CARDS - Energy Insights
# ============================================================
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Opgewekt</div>
        <div class="value green">{energy['opgewekt_kwh']:,.1f} kWh</div>
        <div class="sub dim">{selected_period}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Afgenomen van net</div>
        <div class="value red">{energy['afgenomen_kwh']:,.1f} kWh</div>
        <div class="sub dim">{selected_period}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Teruggeleverd</div>
        <div class="value cyan">{energy['teruggeleverd_kwh']:,.1f} kWh</div>
        <div class="sub dim">{selected_period}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="kpi-card">
        <div class="label">Potentieel (zonder limiet)</div>
        <div class="value yellow">{potential:,.1f} kWh</div>
        <div class="sub orange">Verlies door curtailment: {curtailed:,.1f} kWh</div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# ENERGY CHART - Period overview
# ============================================================
st.markdown("---")

if not df_period.empty:
    df_chart = df_period.copy()
    df_chart["timestamp"] = pd.to_datetime(df_chart["timestamp"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_chart["timestamp"], y=df_chart["inv_total_w"],
        name="Opwek (W)",
        line=dict(color="#00e676", width=2),
        fill="tozeroy", fillcolor="rgba(0,230,118,0.08)",
    ))
    fig.add_trace(go.Scatter(
        x=df_chart["timestamp"], y=df_chart["p1_grid_w"],
        name="Net (W)",
        line=dict(color="#ff5252", width=2),
    ))
    # Zero line
    fig.add_hline(y=0, line_dash="dash", line_color="#333", line_width=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#13161c",
        title=dict(text=f"Vermogen - {selected_period}", font=dict(size=18, color="#b0bec5")),
        xaxis=dict(title="Tijd", gridcolor="#1e2229"),
        yaxis=dict(title="Vermogen (W)", gridcolor="#1e2229"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=400,
        margin=dict(l=60, r=20, t=60, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Geen data voor de geselecteerde periode.")

# ============================================================
# BOTTOM SECTION: Weather + Live Monitor + Details
# ============================================================
st.markdown("---")
bottom_left, bottom_right = st.columns([2, 1])

with bottom_left:
    st.subheader("Weer & Zonpotentieel")
    wcol1, wcol2 = st.columns(2)
    with wcol1:
        st.markdown(f"""
        <div class="period-summary">
            <h4>Vandaag - Veenendaal</h4>
            <div class="stat-row"><span class="lbl">Zonne-uren</span><span class="val">{today_weather['sunshine_hours']} uur</span></div>
            <div class="stat-row"><span class="lbl">Instraling</span><span class="val">{today_weather['radiation_kwh_m2']} kWh/m\u00b2</span></div>
            <div class="stat-row"><span class="lbl">Systeemcapaciteit</span><span class="val">{SYSTEM_KWP:.0f} kWp</span></div>
        </div>
        """, unsafe_allow_html=True)
    with wcol2:
        netto = energy["netto_kwh"]
        netto_color = "red" if netto > 0 else "green"
        netto_label = "Netto afname" if netto > 0 else "Netto teruglevering"
        eigenverbruik = energy["opgewekt_kwh"] - energy["teruggeleverd_kwh"]
        pct_eigen = round(eigenverbruik / energy["opgewekt_kwh"] * 100, 0) if energy["opgewekt_kwh"] > 0 else 0
        st.markdown(f"""
        <div class="period-summary">
            <h4>Samenvatting {selected_period}</h4>
            <div class="stat-row"><span class="lbl">{netto_label}</span><span class="val {netto_color}">{abs(netto):,.1f} kWh</span></div>
            <div class="stat-row"><span class="lbl">Eigenverbruik</span><span class="val">{eigenverbruik:,.1f} kWh ({pct_eigen:.0f}%)</span></div>
            <div class="stat-row"><span class="lbl">Curtailment verlies</span><span class="val orange">{curtailed:,.1f} kWh</span></div>
        </div>
        """, unsafe_allow_html=True)

with bottom_right:
    st.subheader("Live Monitor")
    ts = latest["timestamp"]
    ts_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)
    grid_w = latest["p1_grid_w"] or 0
    solar_w = (latest["inv_40k_actual_w"] or 0) + (latest["inv_50k_actual_w"] or 0)
    if grid_w < 0:
        grid_color = "green"
        grid_label = "Teruglevering"
    elif grid_w == 0:
        grid_color = "dim"
        grid_label = "Neutraal"
    else:
        grid_color = "red"
        grid_label = "Afname"
    inv40_w = latest["inv_40k_actual_w"] or 0
    inv50_w = latest["inv_50k_actual_w"] or 0
    st.markdown(f"""
    <div class="live-mini">
        <div class="stat-row"><span class="lbl">Laatste meting</span><span class="val dim">{ts_str}</span></div>
        <div class="stat-row"><span class="lbl">Zonnestroom</span><span class="val green">{solar_w:,} W</span></div>
        <div class="stat-row"><span class="lbl">Net ({grid_label})</span><span class="val {grid_color}">{grid_w:,} W</span></div>
        <div class="stat-row"><span class="lbl">Solis 40K</span><span class="val">{inv40_w:,} W</span></div>
        <div class="stat-row"><span class="lbl">Solis 50K</span><span class="val">{inv50_w:,} W</span></div>
        <div class="stat-row"><span class="lbl">Limiet</span><span class="val cyan">{latest['total_limit_w'] or 0:,} W</span></div>
    </div>
    """, unsafe_allow_html=True)

# -- Auto-refresh using st.fragment (NO FLICKER) --
# Streamlit 1.45+ supports run_every on fragments
import time

@st.fragment(run_every=REFRESH_SECONDS)
def auto_refresh_trigger():
    """This fragment reruns every N seconds without full page reload."""
    st.empty()

auto_refresh_trigger()
