import os

import altair as alt
import pandas as pd
import streamlit as st

from gobierno_ti.agente import MODELO_POR_DEFECTO, MODELOS, AgenteGobiernoTI
from gobierno_ti.datos import BaseConocimiento
from gobierno_ti.formato import FLECHA, ICONO, ICONO_NIVEL, ICONO_SEVERIDAD
from gobierno_ti.informe import generar_informe
from gobierno_ti.motor import analizar, simular_escenario

PREGUNTAS_SUGERIDAS = [
    "¿Cómo está el gobierno de TI este mes?",
    "¿Cuáles son los riesgos más críticos y qué debe decidir la Junta?",
    "Tenemos 2.000 millones para 2027, ¿en qué proyectos invertimos?",
]

st.set_page_config(page_title="Monarca · Gobierno de TI", page_icon="🦋", layout="wide")
st.session_state.setdefault("chat", [])
try:
    os.environ.setdefault("GEMINI_API_KEY", st.secrets["GEMINI_API_KEY"])
except Exception:
    pass


def limpiar_chat():
    st.session_state.chat = []
    st.session_state.pop("pendiente", None)
    st.session_state.chat_limpiado = True


base = BaseConocimiento()
a = analizar(base)
org = base.organizacion
agente = AgenteGobiernoTI(base)

with st.sidebar:
    st.markdown("## 🦋 Monarca")
    st.caption(f"{org['nombre']} · {org['nota']}")
    listo = agente.disponible
    if listo:
        agente.modelo = st.selectbox("Modelo de Gemini", MODELOS,
                                     index=MODELOS.index(MODELO_POR_DEFECTO) if MODELO_POR_DEFECTO in MODELOS else 0)
    else:
        st.error("Falta la clave de Gemini: guárdala como `GEMINI_API_KEY` en `.streamlit/secrets.toml` "
                 "o, en Streamlit Community Cloud, en los Secrets de la app.")
    if st.button("📄 Redactar informe para la Junta", width="stretch", disabled=not listo):
        with st.spinner("Monarca está redactando el informe..."):
            try:
                st.session_state.informe = generar_informe(base, agente)
            except Exception as exc:
                st.error(f"No se pudo redactar el informe ({type(exc).__name__}: {exc}).")
    if "informe" in st.session_state:
        st.download_button("⬇️ Descargar informe", st.session_state.informe, f"informe_gobierno_ti_{a.periodo}.md",
                           width="stretch")
    st.button("🧹 Limpiar chat", width="stretch", on_click=limpiar_chat)

if st.session_state.pop("chat_limpiado", False):
    st.toast("Conversación borrada. Puedes empezar de nuevo.", icon="🧹")

st.title("🦋 Monarca: agente de Gobierno de TI")
st.caption(f"{org['nombre']} · {org['producto']} · corte {a.periodo} · COBIT 2019 + ISO/IEC 38500")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Índice de salud de TI", f"{a.salud['indice']}/100", help=a.salud["interpretacion"])
c2.metric("KPIs en rojo", f"{sum(e.semaforo == 'rojo' for e in a.estados.values())} de {len(a.estados)}")
c3.metric("Riesgos fuera del apetito", f"{sum(r['fuera_apetito'] for r in a.riesgos)} de {len(a.riesgos)}")
c4.metric("Madurez COBIT vs objetivo", f"{a.madurez['cumplimiento_pct']}%")

tab_agente, tab_kpis, tab_riesgos, tab_decisiones = st.tabs(["🤖 Agente", "📈 Indicadores", "⚠️ Riesgos", "💼 Decisiones"])

with tab_agente:
    for col, sugerida in zip(st.columns(len(PREGUNTAS_SUGERIDAS)), PREGUNTAS_SUGERIDAS):
        if col.button(sugerida, width="stretch", disabled=not listo):
            st.session_state.pendiente = sugerida
    conversacion = st.container()
    pregunta = (st.chat_input("Pregúntale a Monarca...", height=60, disabled=not listo)
                or st.session_state.pop("pendiente", None))
    with conversacion:
        for m in st.session_state.chat:
            st.chat_message(m["role"]).markdown(m["content"])
        if pregunta:
            st.chat_message("user").markdown(pregunta)
            with st.chat_message("assistant"):
                with st.spinner("Monarca está analizando..."):
                    r = agente.responder(pregunta, st.session_state.chat)
                if r.error:
                    st.error(r.texto)
                else:
                    st.markdown(r.texto)
                    if r.herramientas:
                        st.caption("Herramientas usadas: " + ", ".join(h["nombre"] for h in r.herramientas))
            if not r.error:
                st.session_state.chat += [{"role": "user", "content": pregunta}, {"role": "assistant", "content": r.texto}]

with tab_kpis:
    st.dataframe(pd.DataFrame([{"KPI": e.id, "Indicador": e.nombre, "Valor": f"{e.valor:g} {e.unidad}",
                                "Meta": f"{e.meta:g}", "Estado": ICONO[e.semaforo], "Tendencia": FLECHA[e.tendencia],
                                "Responsable": e.responsable} for e in a.estados.values()]),
                 hide_index=True, width="stretch")
    kid = st.selectbox("Histórico", list(a.estados), format_func=lambda k: f"{k} · {base.kpis[k]['nombre']}")
    serie = base.serie(kid).rename_axis("Periodo").reset_index(name="Valor")
    linea = alt.Chart(serie).mark_line(point=True).encode(x="Periodo", y=alt.Y("Valor", scale=alt.Scale(zero=False)))
    meta = alt.Chart(pd.DataFrame({"Meta": [base.kpis[kid]["meta"]]})).mark_rule(color="green", strokeDash=[6, 4]).encode(y="Meta")
    st.altair_chart(linea + meta, width="stretch")
    st.subheader("Alertas")
    for x in a.alertas:
        if x["severidad"] in ("crítica", "alta"):
            st.markdown(f"{ICONO_SEVERIDAD[x['severidad']]} **{x['kpi']} {x['nombre']}**: {'; '.join(x['senales'])}")

with tab_riesgos:
    st.caption(f"Apetito de riesgo: {base.apetito_riesgo}. {base.regla_ajuste_kri}")
    st.dataframe(pd.DataFrame([{"ID": r["id"], "Riesgo": r["nombre"], "Residual": r["puntaje_residual"],
                                "Ajustado por KRI": r["puntaje_ajustado"], "Nivel": f"{ICONO_NIVEL[r['nivel']]} {r['nivel']}",
                                "Fuera del apetito": "Sí" if r["fuera_apetito"] else "No", "Dueño": r["dueno"]}
                               for r in a.riesgos]), hide_index=True, width="stretch")

with tab_decisiones:
    presupuesto = st.slider("Presupuesto 2027 (millones COP)", 500, 5000, int(base.presupuesto_disponible), step=50)
    pr = analizar(base, presupuesto=presupuesto).priorizacion
    sim = simular_escenario(base, a.riesgos, pr["seleccionados"])
    st.dataframe(pd.DataFrame([{"#": p["prioridad"], "Proyecto": f"{p['id']} {p['nombre']}", "Costo (M)": p["presupuesto"],
                                "Puntaje": p["puntaje"], "Riesgos que mitiga": ", ".join(p["riesgos_mitigados"]),
                                "Decisión": "✅ Financiar" if p["financiado"] else f"⏸ Faltan {p['faltante']:g} M"}
                               for p in pr["ranking"]]), hide_index=True, width="stretch")
    st.info(f"Financiar {', '.join(pr['seleccionados'])} cuesta {pr['total_seleccionado']:g} M COP y reduce la exposición "
            f"al riesgo en {sim['reduccion_pct']}% (riesgos fuera del apetito: {sim['fuera_apetito_antes']} → "
            f"{sim['fuera_apetito_despues']}).")
    st.subheader("Plan de acción")
    st.dataframe(pd.DataFrame([{"Severidad": ICONO_SEVERIDAD[x["severidad"]], "Acción": x["accion"],
                                "Decide": x["instancia"], "Responsable": x["responsable"], "Plazo": x["plazo"]}
                               for x in a.acciones[:10]]), hide_index=True, width="stretch")
