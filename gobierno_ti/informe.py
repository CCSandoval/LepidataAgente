from collections import Counter
from datetime import date

from .datos import BaseConocimiento
from .formato import ICONO, ICONO_NIVEL, ICONO_SEVERIDAD
from .motor import Analisis, analizar, simular_escenario


def _hechos(base: BaseConocimiento, a: Analisis, sim: dict) -> str:
    conteo = Counter(e.semaforo for e in a.estados.values())
    criticas = [x for x in a.alertas if x["severidad"] == "crítica"]
    fuera = [r for r in a.riesgos if r["fuera_apetito"]]
    criticos = [r["id"] for r in fuera if r["nivel"] == "Crítico"]
    texto = (
        f"El índice de salud del gobierno de TI de {base.organizacion['nombre']} es {a.salud['indice']}/100 "
        f"({a.salud['interpretacion'].lower()}). {conteo.get('rojo', 0)} de {len(a.estados)} indicadores incumplen su meta"
    )
    if criticas:
        texto += ", con alertas críticas en " + ", ".join(f"{x['kpi']} ({x['nombre']})" for x in criticas[:3])
    texto += (
        f". {len(fuera)} riesgos superan el apetito ({base.apetito_riesgo})"
        + (f", de los cuales {len(criticos)} son críticos ({', '.join(criticos)})" if criticos else "")
        + f". La madurez COBIT alcanza el {a.madurez['cumplimiento_pct']}% del nivel objetivo"
        + (f", con brechas mayores en {', '.join(a.madurez['brechas_mayores'][:4])}" if a.madurez["brechas_mayores"] else "")
        + f". Se recomienda aprobar el portafolio {', '.join(a.priorizacion['seleccionados'])} "
          f"({a.priorizacion['total_seleccionado']:g} M COP), que reduce la exposición al riesgo en {sim['reduccion_pct']}%."
    )
    return texto


def generar_informe(base: BaseConocimiento, agente) -> str:
    a = analizar(base)
    sim = simular_escenario(base, a.riesgos, a.priorizacion["seleccionados"])
    decisiones = "\n".join(f"- {x['accion']} ({x['instancia']}, {x['plazo']})" for x in a.acciones[:5])
    resumen = agente.redactar_resumen_ejecutivo(f"Hechos:\n{_hechos(base, a, sim)}\n\nDecisiones propuestas:\n{decisiones}")
    autor = f"Monarca con Gemini ({agente.modelo})"

    org = base.organizacion
    L = [
        f"# Informe de Gobierno de TI: {org['nombre']}",
        "",
        f"**Periodo de corte:** {a.periodo} · **Generado:** {date.today().isoformat()} · "
        f"**Elaborado por:** Monarca, agente inteligente de gobierno de TI · **Dirigido a:** Junta Directiva y Comité de Gobierno de TI y Producto",
        "",
        f"> {org['nota']}",
        "",
        "## 1. Resumen ejecutivo",
        "",
        resumen,
        "",
        f"_Redactado por: {autor}._",
        "",
        "## 2. Evaluar: estado del gobierno de TI",
        "",
        f"**Índice de salud: {a.salud['indice']}/100 ({a.salud['interpretacion']})**",
        "",
        "| Componente | Puntaje | Peso |",
        "|---|---|---|",
        *[f"| {k} | {v['puntaje']} | {int(v['peso'] * 100)}% |" for k, v in a.salud["componentes"].items()],
        "",
        "**Principios ISO/IEC 38500**",
        "",
        "| Principio | Puntaje | KPI en rojo | Mecanismo de gobierno |",
        "|---|---|---|---|",
        *[f"| {p['principio']} | {p['puntaje']} | {', '.join(p['kpis_en_rojo']) or '-'} | {p['mecanismo']} |" for p in a.principios],
        "",
        "**Madurez de procesos COBIT 2019**",
        "",
        "| Proceso | Diagnóstico | Actual | Objetivo | Brecha |",
        "|---|---|---|---|---|",
        *[f"| {p['id']} {p['nombre']} | {p['nivel_diagnostico']} | {p['nivel_actual']} | {p['nivel_objetivo']} | {p['brecha']} |"
          for p in a.madurez["procesos"]],
        "",
        "## 3. Monitorear: indicadores y alertas",
        "",
        "| KPI | Indicador | Valor | Meta | Estado | Tendencia | Proyección |",
        "|---|---|---|---|---|---|---|",
        *[f"| {e.id} | {e.nombre} | {e.valor:g} {e.unidad} | {e.meta:g} | {ICONO[e.semaforo]} | {e.tendencia} | {e.proyeccion:.4g} |"
          for e in a.estados.values()],
        "",
        "**Alertas críticas y altas**",
        "",
        *[f"- {ICONO_SEVERIDAD[x['severidad']]} **{x['kpi']} {x['nombre']}** ({x['responsable']}): {'; '.join(x['senales'])}"
          for x in a.alertas if x["severidad"] in ("crítica", "alta")],
        "",
        "## 4. Identificar: riesgos de TI e IA",
        "",
        f"Apetito de riesgo: residual máximo de {base.apetito_riesgo}. {base.regla_ajuste_kri}",
        "",
        "| ID | Riesgo | Inherente | Residual | Ajustado por KRI | Nivel | Estado | Dueño |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {r['id']} | {r['nombre']} | {r['puntaje_inherente']} | {r['puntaje_residual']} | **{r['puntaje_ajustado']:g}** | "
          f"{ICONO_NIVEL[r['nivel']]} {r['nivel']} | {r['estado']} | {r['dueno']} |" for r in a.riesgos],
        "",
        *([f"**Señales de riesgo emergente:** " + " ".join(f"{e['referencia']}: {e['detalle']}" for e in a.emergentes), ""]
          if a.emergentes else []),
        "## 5. Dirigir: portafolio y decisiones requeridas",
        "",
        f"Presupuesto disponible 2027: {a.priorizacion['presupuesto']:g} M COP.",
        "",
        "| # | Proyecto | Costo | Puntaje | Decisión |",
        "|---|---|---|---|---|",
        *[f"| {p['prioridad']} | {p['id']} {p['nombre']} | {p['presupuesto']:g} | {p['puntaje']} | "
          f"{'Financiar' if p['financiado'] else 'Sin cupo (faltan ' + format(p['faltante'], 'g') + ')'} |"
          for p in a.priorizacion["ranking"]],
        "",
        f"Con el portafolio recomendado la exposición total al riesgo pasa de {sim['exposicion_antes']:g} a "
        f"{sim['exposicion_despues']:g} (−{sim['reduccion_pct']}%) y los riesgos fuera del apetito de "
        f"{sim['fuera_apetito_antes']} a {sim['fuera_apetito_despues']}.",
        "",
        "**Proyectos en ejecución**",
        "",
        *[f"- {ICONO[p['semaforo']]} {p['id']} {p['nombre']}: avance {p['avance_real']}% vs {p['avance_planeado']}% "
          f"planeado (SPI {p['spi']}, CPI {p['cpi']})" for p in a.portafolio],
        "",
        "**Plan de acción priorizado**",
        "",
        "| # | Acción | Decide | Responsable | Plazo | COBIT / ISO 38500 |",
        "|---|---|---|---|---|---|",
        *[f"| {i} | {x['accion']} | {x['instancia']} | {x['responsable']} | {x['plazo']} | {x['proceso_cobit']} / {x['principio_iso38500']} |"
          for i, x in enumerate(a.acciones[:12], start=1)],
        "",
        "---",
        "_Metodología: semáforo por meta y umbral, tendencia por regresión lineal de los últimos 4 periodos, proyección "
        "lineal con 6 periodos, riesgo residual ajustado por KRI, priorización por matriz ponderada aprobada por el Comité._",
    ]
    return "\n".join(L)
