import streamlit as st
import requests
import os
import pandas as pd
import plotly.express as px
from datetime import datetime

API_URL = os.getenv("API_URL", "https://pv-monitor-api-production.up.railway.app")

st.set_page_config(page_title="PV Monitor Dashboard", page_icon="☀️", layout="wide")

# Session state
if "token" not in st.session_state:
    st.session_state.token = None
if "role" not in st.session_state:
    st.session_state.role = None

def login(username, password):
    try:
        r = requests.post(f"{API_URL}/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            data = r.json()
            st.session_state.token = data["access_token"]
            st.session_state.role = data.get("role", "client")
            return True
    except Exception:
        pass
    return False

def headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}

# Login page
if not st.session_state.token:
    st.title("☀️ PV Monitor - Login")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        username = st.text_input("Gebruikersnaam")
        password = st.text_input("Wachtwoord", type="password")
        if st.button("Inloggen", type="primary"):
            if login(username, password):
                st.rerun()
            else:
                st.error("Login mislukt")
else:
    # Sidebar
    st.sidebar.title("☀️ PV Monitor")
    st.sidebar.write(f"Rol: **{st.session_state.role}**")
    if st.sidebar.button("Uitloggen"):
        st.session_state.token = None
        st.session_state.role = None
        st.rerun()

    # Admin view
    if st.session_state.role == "admin":
        page = st.sidebar.selectbox("Pagina", ["Dashboard", "Systemen", "Gebruikers", "Batterij Simulatie"])
    else:
        page = st.sidebar.selectbox("Pagina", ["Dashboard", "Batterij Simulatie"])

    system_id = st.sidebar.text_input("Systeem ID", value="system-001")

    if page == "Dashboard":
        st.title("📊 Live Dashboard")
        try:
            r = requests.get(f"{API_URL}/api/live/{system_id}", headers=headers())
            if r.status_code == 200:
                data = r.json()
                live = data.get("live", {})
                fin = data.get("financials", {})
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Productie", f"{live.get('production_total', 0)} W")
                col2.metric("Eigen Verbruik", f"{live.get('self_consumption_w', 0)} W")
                col3.metric("Netlevering", f"{live.get('grid', 0)} W")
                col4.metric("Besparing Vandaag", f"€{fin.get('savings_today_euro', 0):.2f}")
                st.subheader("Tarieven")
                st.write(f"Huidig tarief: **€{fin.get('current_rate_euro', 0):.4f}/kWh**")
                st.json(data)
            else:
                st.warning("Geen data beschikbaar")
        except Exception as e:
            st.error(f"API fout: {e}")

    elif page == "Batterij Simulatie":
        st.title("🔋 Batterij Simulatie")
        col1, col2 = st.columns(2)
        with col1:
            capacity = st.number_input("Capaciteit (kWh)", value=10.0, step=1.0)
            max_charge = st.number_input("Max laadvermogen (kW)", value=5.0, step=0.5)
        with col2:
            efficiency = st.slider("Rendement (%)", 80, 100, 95) / 100
            days = st.number_input("Dagen", value=30, step=1)
        if st.button("Simuleer", type="primary"):
            try:
                r = requests.post(f"{API_URL}/api/simulate", headers=headers(), json={
                    "system_id": system_id, "battery_capacity_kwh": capacity,
                    "max_charge_rate_kw": max_charge, "efficiency": efficiency, "days": days
                })
                if r.status_code == 200:
                    result = r.json()
                    st.success("Simulatie voltooid!")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Extra besparing", f"€{result.get('total_savings_eur', 0):.2f}")
                    c2.metric("Zelfconsumptie +", f"{result.get('self_consumption_increase_pct', 0):.1f}%")
                    c3.metric("ROI (jaren)", f"{result.get('roi_years', 0):.1f}")
                    st.json(result)
                else:
                    st.error("Simulatie mislukt")
            except Exception as e:
                st.error(f"Fout: {e}")

    elif page == "Systemen" and st.session_state.role == "admin":
        st.title("⚙️ Systemen Beheer")
        try:
            r = requests.get(f"{API_URL}/admin/systems", headers=headers())
            if r.status_code == 200:
                st.dataframe(pd.DataFrame(r.json()), use_container_width=True)
        except Exception as e:
            st.error(f"Fout: {e}")

    elif page == "Gebruikers" and st.session_state.role == "admin":
        st.title("👥 Gebruikers Beheer")

        # Bestaande gebruikers
        try:
            r = requests.get(f"{API_URL}/admin/users", headers=headers())
            if r.status_code == 200:
                st.subheader("Huidige Gebruikers")
                st.dataframe(pd.DataFrame(r.json()), use_container_width=True)
        except Exception as e:
            st.error(f"Fout: {e}")

        # Nieuw account aanmaken
        st.subheader("Nieuw Account Aanmaken")
        with st.form("create_user"):
            new_email = st.text_input("E-mail")
            new_password = st.text_input("Wachtwoord", type="password")
            new_role = st.selectbox("Rol", ["client", "admin"])
            submitted = st.form_submit_button("Account Aanmaken", type="primary")
            if submitted:
                if new_email and new_password:
                    try:
                        r = requests.post(
                            f"{API_URL}/admin/users",
                            headers=headers(),
                            json={"email": new_email, "password": new_password, "role": new_role}
                        )
                        if r.status_code == 200:
                            st.success(f"Account {new_email} aangemaakt!")
                            st.rerun()
                        else:
                            st.error(f"Fout: {r.text}")
                    except Exception as e:
                        st.error(f"Fout: {e}")
                else:
                    st.warning("Vul alle velden in")
