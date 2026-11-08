import re
from dataclasses import dataclass

import numpy as np

from .datos import BaseConocimiento

PUNTAJE_SEMAFORO = {"verde": 100, "amarillo": 50, "rojo": 0}
ORDEN_SEVERIDAD = {"crítica": 4, "alta": 3, "media": 2, "baja": 1}
PLAZO_POR_SEVERIDAD = {"crítica": "30 días", "alta": "60 días", "media": "90 días", "baja": "Próximo trimestre"}
INSTANCIA_GENERAL = "Comité de Gobierno de TI y Producto"
INSTANCIA_POR_PROCESO = {"APO13": "Comité de Seguridad y Privacidad", "DSS05": "Comité de Seguridad y Privacidad",
                         "BAI03": "Comité de Ética y Calidad de IA", "APO14": "Comité de Ética y Calidad de IA"}


def semaforo(valor: float, meta: float, umbral: float, sentido: str) -> str:
    if sentido != "max":
        valor, meta, umbral = -valor, -meta, -umbral
    return "verde" if valor >= meta else "amarillo" if valor >= umbral else "rojo"


def _pendiente(v: list[float]) -> float:
    return float(np.polyfit(np.arange(len(v)), v, 1)[0]) if len(v) > 1 else 0.0


def tendencia(v: list[float], meta: float, umbral: float, sentido: str) -> str:
    m = _pendiente(v[-4:]) * (1 if sentido == "max" else -1)
    tolerancia = 0.1 * abs(meta - umbral)
    return "mejorando" if m > tolerancia else "deteriorando" if m < -tolerancia else "estable"


def periodos_deterioro(v: list[float], sentido: str) -> int:
    n = 0
    for anterior, actual in zip(reversed(v[:-1]), reversed(v[1:])):
        if not (actual < anterior if sentido == "max" else actual > anterior):
            break
        n += 1
    return n


def proyectar(v: list[float], unidad: str) -> float:
    r = v[-6:]
    if len(r) < 2:
        return r[-1]
    a, b = np.polyfit(np.arange(len(r)), r, 1)
    p = float(a * len(r) + b)
    return max(0.0, min(100.0, p) if unidad == "%" else p)


def variacion_atipica(v: list[float]) -> float | None:
    difs = np.diff(v)
    if len(difs) < 5 or difs[:-1].std() == 0:
        return None
    return float((difs[-1] - difs[:-1].mean()) / difs[:-1].std())


def _fmt(valor: float, unidad: str) -> str:
    return f"{valor:g}%" if unidad == "%" else f"USD {valor:.2f}" if unidad == "USD" else f"{valor:g} {unidad}"


@dataclass
class EstadoKPI:
    id: str
    nombre: str
    unidad: str
    meta: float
    umbral: float
    sentido: str
    proceso: str
    objetivo: str
    responsable: str
    valor: float
    semaforo: str
    tendencia: str
    periodos_deterioro: int
    proyeccion: float
    semaforo_proyectado: str
    z_variacion: float | None


def evaluar_kpis(base: BaseConocimiento) -> dict[str, EstadoKPI]:
    estados = {}
    for kid, k in base.kpis.items():
        v = [float(x) for x in base.serie(kid)]
        if not v:
            continue
        criterio = (k["meta"], k["umbral"], k["sentido"])
        proy = proyectar(v, k["unidad"])
        estados[kid] = EstadoKPI(
            kid, *(k[c] for c in ("nombre", "unidad", "meta", "umbral", "sentido", "proceso", "objetivo", "responsable")),
            valor=v[-1], semaforo=semaforo(v[-1], *criterio), tendencia=tendencia(v, *criterio),
            periodos_deterioro=periodos_deterioro(v, k["sentido"]), proyeccion=proy,
            semaforo_proyectado=semaforo(proy, *criterio), z_variacion=variacion_atipica(v))
    return estados


def _deteriora(e: EstadoKPI) -> bool:
    return e.tendencia == "deteriorando" or e.periodos_deterioro >= 3


def detectar_alertas(base: BaseConocimiento, estados: dict[str, EstadoKPI]) -> list[dict]:
    alertas = []
    for e in estados.values():
        senales = []
        if e.semaforo == "rojo":
            senales.append(f"Incumple la meta ({_fmt(e.valor, e.unidad)} vs meta {_fmt(e.meta, e.unidad)})")
        elif e.semaforo == "amarillo":
            senales.append(f"En zona de alerta ({_fmt(e.valor, e.unidad)}, meta {_fmt(e.meta, e.unidad)})")
        if e.periodos_deterioro >= 3:
            senales.append(f"Deterioro sostenido: {e.periodos_deterioro} periodos consecutivos empeorando")
        elif e.tendencia == "deteriorando":
            senales.append("Tendencia reciente de deterioro")
        if e.semaforo != "rojo" and e.semaforo_proyectado == "rojo":
            senales.append(f"Proyección de incumplimiento el próximo periodo ({_fmt(e.proyeccion, e.unidad)})")
        z = e.z_variacion
        if z is not None and (z < 0 if e.sentido == "max" else z > 0) and abs(z) >= 2.5:
            senales.append(f"Variación atípica en el último periodo (z = {z:.1f})")
        if not senales:
            continue
        if e.semaforo == "rojo":
            severidad = "crítica" if _deteriora(e) else "alta"
        elif e.semaforo == "amarillo":
            severidad = "alta" if _deteriora(e) or e.semaforo_proyectado == "rojo" else "media"
        else:
            severidad = "media" if _deteriora(e) or e.semaforo_proyectado != "verde" else "baja"
        alertas.append({"kpi": e.id, "nombre": e.nombre, "severidad": severidad, "semaforo": e.semaforo,
                        "valor": round(e.valor, 2), "meta": e.meta, "unidad": e.unidad, "senales": senales,
                        "proceso": e.proceso, "responsable": e.responsable,
                        "acciones_sugeridas": base.kpis[e.id].get("acciones_sugeridas", [])})
    return sorted(alertas, key=lambda a: (-ORDEN_SEVERIDAD[a["severidad"]], a["kpi"]))


def nivel_riesgo(base: BaseConocimiento, puntaje: float) -> str:
    return next((n["nivel"] for n in base.niveles_riesgo if puntaje <= n["max"]), base.niveles_riesgo[-1]["nivel"])


def evaluar_riesgos(base: BaseConocimiento, estados: dict[str, EstadoKPI]) -> list[dict]:
    resultado = []
    for r in base.riesgos.values():
        kris = [{"kri": e.id, "semaforo": e.semaforo, "tendencia": e.tendencia,
                 "ajuste": {"rojo": 1.0, "amarillo": 0.5, "verde": 0.0}[e.semaforo]
                 + {"deteriorando": 0.5, "mejorando": -0.5}.get(e.tendencia, 0.0)}
                for e in (estados[k] for k in r["kris"] if k in estados)]
        ajuste = round(float(np.mean([d["ajuste"] for d in kris])), 2) if kris else 0.0
        prob = round(min(5.0, max(1.0, r["prob_residual"] + ajuste)), 2)
        residual = r["prob_residual"] * r["impacto_residual"]
        ajustado = round(prob * r["impacto_residual"], 1)
        nivel, fuera = nivel_riesgo(base, ajustado), ajustado > base.apetito_riesgo
        observaciones = []
        if fuera:
            observaciones.append(f"Fuera del apetito de riesgo (> {base.apetito_riesgo})")
        if nivel != nivel_riesgo(base, residual):
            observaciones.append(f"Cambió de nivel {nivel_riesgo(base, residual)} a {nivel} por sus KRI")
        if not r["kris"]:
            observaciones.append("No tiene KRI asociado: no se puede monitorear")
        if r["tratamiento"] == "Sin definir":
            observaciones.append("Sin plan de tratamiento definido")
        if not r["controles"]:
            observaciones.append("Sin controles actuales")
        if r["estado"] == "Abierto" and fuera:
            observaciones.append("Abierto y fuera del apetito: requiere decisión del Comité")
        resultado.append({**r, "kris": kris, "prob_ajustada": prob, "puntaje_inherente": r["probabilidad"] * r["impacto"],
                          "puntaje_residual": residual, "puntaje_ajustado": ajustado, "nivel": nivel,
                          "fuera_apetito": fuera, "observaciones": observaciones})
    return sorted(resultado, key=lambda x: (-x["puntaje_ajustado"], x["id"]))


def riesgos_emergentes(estados: dict[str, EstadoKPI], riesgos: list[dict]) -> list[dict]:
    vinculados = {}
    for r in riesgos:
        for d in r["kris"]:
            vinculados.setdefault(d["kri"], []).append(r)
    hallazgos = []
    for e in estados.values():
        asociados = vinculados.get(e.id, [])
        if not asociados and (e.semaforo == "rojo" or _deteriora(e)):
            hallazgos.append({"tipo": "KPI sin riesgo asociado", "referencia": e.id,
                              "detalle": f"{e.nombre} está en {e.semaforo} y no es KRI de ningún riesgo. Evaluar si hay un riesgo no registrado."})
        elif e.semaforo == "rojo" and asociados and not any(r["fuera_apetito"] for r in asociados):
            hallazgos.append({"tipo": "Calificación posiblemente subestimada", "referencia": e.id,
                              "detalle": f"{e.nombre} está en rojo pero sus riesgos ({', '.join(r['id'] for r in asociados)}) siguen dentro del apetito. Revisar la calificación."})
    return hallazgos + [{"tipo": "Riesgo sin KRI", "referencia": r["id"],
                         "detalle": f"{r['nombre']} no tiene indicador de riesgo; definir un KRI para monitorearlo."}
                        for r in riesgos if not r["kris"]]


def simular_escenario(base: BaseConocimiento, riesgos: list[dict], proyecto_ids: list[str]) -> dict:
    prob = {r["id"]: r["prob_ajustada"] for r in riesgos}
    impacto = {r["id"]: r["impacto_residual"] for r in riesgos}
    aplicados = [p for p in proyecto_ids if p in base.proyectos]
    for pid in aplicados:
        for m in base.proyectos[pid]["mitiga"]:
            prob[m["riesgo"]] = max(1.0, prob[m["riesgo"]] - m["reduccion_prob"])
    cambios = []
    for r in riesgos:
        despues = round(prob[r["id"]] * impacto[r["id"]], 1)
        if despues != r["puntaje_ajustado"]:
            cambios.append({"riesgo": r["id"], "nombre": r["nombre"], "antes": r["puntaje_ajustado"], "despues": despues,
                            "nivel_antes": r["nivel"], "nivel_despues": nivel_riesgo(base, despues)})
    antes = round(sum(r["puntaje_ajustado"] for r in riesgos), 1)
    despues = round(sum(prob[i] * impacto[i] for i in prob), 1)
    return {
        "proyectos": aplicados,
        "inversion": sum(base.proyectos[p]["presupuesto"] for p in aplicados),
        "exposicion_antes": antes,
        "exposicion_despues": despues,
        "reduccion_pct": round(100 * (antes - despues) / antes, 1) if antes else 0.0,
        "fuera_apetito_antes": sum(r["puntaje_ajustado"] > base.apetito_riesgo for r in riesgos),
        "fuera_apetito_despues": sum(prob[i] * impacto[i] > base.apetito_riesgo for i in prob),
        "cambios": cambios,
    }


def _cumplimiento(p: dict) -> float:
    return min(1, p["nivel_actual"] / p["nivel_objetivo"])


def evaluar_madurez(base: BaseConocimiento, estados: dict[str, EstadoKPI]) -> dict:
    procesos, dominios = [], {}
    for p in base.procesos.values():
        kpis = [e for e in estados.values() if e.proceso == p["id"]]
        rango = p["nivel_objetivo"] - p["nivel_diagnostico"]
        procesos.append({**p, "brecha": p["nivel_objetivo"] - p["nivel_actual"],
                         "progreso_pct": round(100 * (p["nivel_actual"] - p["nivel_diagnostico"]) / rango) if rango > 0 else 100,
                         "kpis_en_verde_pct": round(100 * sum(e.semaforo == "verde" for e in kpis) / len(kpis)) if kpis else None})
        dominios.setdefault(p["dominio"], []).append(p)
    return {
        "procesos": procesos,
        "dominios": {d: {"actual": round(float(np.mean([p["nivel_actual"] for p in ps])), 2),
                         "objetivo": round(float(np.mean([p["nivel_objetivo"] for p in ps])), 2)}
                     for d, ps in dominios.items()},
        "cumplimiento_pct": round(100 * float(np.mean([_cumplimiento(p) for p in procesos]))),
        "brechas_mayores": [p["id"] for p in sorted(procesos, key=lambda x: -x["brecha"]) if p["brecha"] >= 2],
    }


def evaluar_principios(base: BaseConocimiento, estados: dict[str, EstadoKPI]) -> list[dict]:
    resultado = []
    for pr in base.modelo_gobierno["principios_iso38500"]:
        kpis = [estados[k] for k in pr.get("kpis", []) if k in estados]
        procs = [base.procesos[p] for p in pr.get("procesos", []) if p in base.procesos]
        partes = [float(np.mean(x)) for x in ([PUNTAJE_SEMAFORO[e.semaforo] for e in kpis],
                                              [100 * _cumplimiento(p) for p in procs]) if x]
        resultado.append({"principio": pr["principio"], "mecanismo": pr["mecanismo"],
                          "puntaje": round(float(np.mean(partes))) if partes else None,
                          "kpis_en_rojo": [e.id for e in kpis if e.semaforo == "rojo"]})
    return resultado


def evaluar_portafolio(base: BaseConocimiento) -> list[dict]:
    resultado = []
    for p in base.proyectos.values():
        if p["estado"] != "En ejecución":
            continue
        spi = p["avance_real"] / p["avance_planeado"] if p["avance_planeado"] else 1.0
        cpi = (p["avance_real"] / 100 * p["presupuesto"]) / p["ejecutado"] if p["ejecutado"] else 1.0
        peor = min(spi, cpi)
        resultado.append({**p, "spi": round(spi, 2), "cpi": round(cpi, 2),
                          "semaforo": "verde" if peor >= 0.95 else "amarillo" if peor >= 0.85 else "rojo"})
    return resultado


def priorizar_proyectos(base: BaseConocimiento, riesgos: list[dict], presupuesto: float | None = None) -> dict:
    presupuesto = base.presupuesto_disponible if presupuesto is None else presupuesto
    pesos = base.pesos_priorizacion
    riesgo = {r["id"]: r for r in riesgos}
    candidatos = [p for p in base.proyectos.values() if p["estado"] == "Propuesto"]
    reduccion = {p["id"]: sum((riesgo[m["riesgo"]]["prob_ajustada"]
                               - max(1.0, riesgo[m["riesgo"]]["prob_ajustada"] - m["reduccion_prob"]))
                              * riesgo[m["riesgo"]]["impacto_residual"] for m in p["mitiga"]) for p in candidatos}
    max_red = max(reduccion.values(), default=0) or 1
    ranking = []
    for p in candidatos:
        criterios = {"alineacion": p["alineacion"], "reduccion_riesgo": round(1 + 4 * reduccion[p["id"]] / max_red, 2),
                     "valor": p["valor"], "urgencia_regulatoria": p["urgencia_regulatoria"], "facilidad": 6 - p["complejidad"]}
        ranking.append({"id": p["id"], "nombre": p["nombre"], "presupuesto": p["presupuesto"],
                        "riesgos_mitigados": [m["riesgo"] for m in p["mitiga"]],
                        "puntaje": round(sum(pesos[c] * v for c, v in criterios.items()) / sum(pesos.values()), 2)})
    ranking.sort(key=lambda x: (-x["puntaje"], x["presupuesto"]))
    saldo = presupuesto
    for i, p in enumerate(ranking, start=1):
        p["prioridad"], p["financiado"] = i, p["presupuesto"] <= saldo
        p["faltante"] = 0 if p["financiado"] else p["presupuesto"] - saldo
        saldo -= p["presupuesto"] if p["financiado"] else 0
    return {"presupuesto": presupuesto, "pesos": pesos, "ranking": ranking,
            "seleccionados": [p["id"] for p in ranking if p["financiado"]],
            "total_seleccionado": presupuesto - saldo, "saldo": saldo}


def indice_salud(estados: dict[str, EstadoKPI], riesgos: list[dict], madurez: dict, portafolio: list[dict]) -> dict:
    peor = np.mean([min(1.0, p["spi"], p["cpi"]) for p in portafolio]) if portafolio else None
    componentes = {
        "Desempeño de indicadores": (float(np.mean([PUNTAJE_SEMAFORO[e.semaforo] for e in estados.values()])), 0.30),
        "Madurez de procesos COBIT": (float(madurez["cumplimiento_pct"]), 0.25),
        "Exposición al riesgo": (100 * (1 - (float(np.mean([r["puntaje_ajustado"] for r in riesgos])) - 1) / 24), 0.25),
        "Desempeño del portafolio": (float(np.clip((peor - 0.7) / 0.3 * 100, 0, 100)) if portafolio else 100.0, 0.20),
    }
    total = sum(v * w for v, w in componentes.values())
    interpretacion = ("Sólido" if total >= 80 else "Aceptable con brechas" if total >= 60
                      else "Débil: requiere intervención" if total >= 40 else "Crítico")
    return {"indice": round(total, 1), "interpretacion": interpretacion,
            "componentes": {k: {"puntaje": round(v, 1), "peso": w} for k, (v, w) in componentes.items()}}


def recomendar_acciones(base: BaseConocimiento, alertas: list[dict], riesgos: list[dict], madurez: dict,
                        portafolio: list[dict], priorizacion: dict) -> list[dict]:
    acciones = []

    def agregar(prioridad, severidad, accion, justificacion, responsable, instancia, proceso, referencia):
        principio = next((pr["principio"] for pr in base.modelo_gobierno["principios_iso38500"]
                          if proceso in pr.get("procesos", [])), "Desempeño")
        acciones.append({"prioridad": prioridad, "severidad": severidad, "accion": accion,
                         "justificacion": justificacion, "responsable": responsable, "instancia": instancia,
                         "proceso_cobit": proceso, "principio_iso38500": principio,
                         "plazo": PLAZO_POR_SEVERIDAD[severidad], "referencia": referencia})

    usados: set[str] = set()
    for a in alertas:
        if a["severidad"] not in ("crítica", "alta"):
            continue
        accion = next((s for s in a["acciones_sugeridas"] if not set(re.findall(r"P\d{2}", s)) & usados), None)
        if accion is None:
            continue
        usados |= set(re.findall(r"P\d{2}", accion))
        agregar(60 + 10 * ORDEN_SEVERIDAD[a["severidad"]], a["severidad"], accion,
                f"{a['kpi']} {a['nombre']}: " + "; ".join(a["senales"]), a["responsable"],
                INSTANCIA_POR_PROCESO.get(a["proceso"], INSTANCIA_GENERAL), a["proceso"], a["kpi"])

    for r in riesgos:
        if not r["fuera_apetito"]:
            continue
        mitigadores = [p["id"] for p in base.proyectos.values() if any(m["riesgo"] == r["id"] for m in p["mitiga"])]
        if r["nivel"] == "Crítico":
            agregar(95, "crítica", f"Escalar a la Junta Directiva el riesgo {r['id']} y aprobar su tratamiento"
                    + (f" (proyectos: {', '.join(mitigadores)})" if mitigadores else ""),
                    f"{r['nombre']}: puntaje ajustado {r['puntaje_ajustado']:g} (nivel Crítico, apetito {base.apetito_riesgo}).",
                    r["dueno"], "Junta Directiva", r["procesos"][0], r["id"])
        elif r["estado"] == "Abierto" or r["tratamiento"] == "Sin definir":
            agregar(85, "alta", f"Definir y aprobar el plan de tratamiento del riesgo {r['id']}"
                    + (f"; evaluar los proyectos {', '.join(mitigadores)}" if mitigadores else ""),
                    f"{r['nombre']}: estado {r['estado']}, tratamiento {r['tratamiento']}, puntaje {r['puntaje_ajustado']:g}.",
                    r["dueno"], INSTANCIA_GENERAL, r["procesos"][0], r["id"])

    for p in portafolio:
        if p["semaforo"] != "verde":
            rojo = p["semaforo"] == "rojo"
            agregar(70 if rojo else 50, "alta" if rojo else "media", f"Revisar el plan y los recursos del proyecto {p['id']}",
                    f"{p['nombre']}: SPI {p['spi']} y CPI {p['cpi']} (avance {p['avance_real']}% vs {p['avance_planeado']}% planeado).",
                    "PMO", INSTANCIA_GENERAL, "BAI01", p["id"])

    if priorizacion["seleccionados"]:
        agregar(80, "alta", f"Aprobar el portafolio {', '.join(priorizacion['seleccionados'])} para 2027",
                f"Mayor puntaje en la matriz ponderada; usa {priorizacion['total_seleccionado']:g} de "
                f"{priorizacion['presupuesto']:g} millones COP disponibles.",
                "Comité de Gobierno de TI y Producto", "Junta Directiva", "APO05", "Portafolio")
    for p in priorizacion["ranking"][:5]:
        if p["financiado"]:
            continue
        diferibles = [q["id"] for q in priorizacion["ranking"] if q["financiado"] and q["prioridad"] > p["prioridad"]]
        agregar(75, "alta", f"Evaluar una adición presupuestal de {p['faltante']:g} millones COP"
                + (f" o diferir {', '.join(diferibles)}" if diferibles else "") + f" para financiar {p['id']}",
                f"{p['nombre']} tiene prioridad {p['prioridad']} (puntaje {p['puntaje']}) y mitiga "
                f"{', '.join(p['riesgos_mitigados']) or 'ningún riesgo registrado'}, pero no cabe en el saldo disponible.",
                "CFO", "Comité de Gobierno de TI y Producto", "EDM02", p["id"])

    for p in madurez["procesos"]:
        if p["brecha"] >= 2:
            agregar(40, "media", f"Fortalecer el proceso {p['id']} ({p['nombre']}) del nivel {p['nivel_actual']} al {p['nivel_objetivo']}",
                    f"Brecha de capacidad de {p['brecha']} niveles; progreso desde el diagnóstico: {p['progreso_pct']}%.",
                    p["responsable"], INSTANCIA_GENERAL, p["id"], p["id"])

    return sorted(acciones, key=lambda a: -a["prioridad"])


@dataclass
class Analisis:
    periodo: str
    estados: dict[str, EstadoKPI]
    alertas: list[dict]
    riesgos: list[dict]
    emergentes: list[dict]
    madurez: dict
    principios: list[dict]
    portafolio: list[dict]
    priorizacion: dict
    salud: dict
    acciones: list[dict]


def analizar(base: BaseConocimiento, presupuesto: float | None = None) -> Analisis:
    estados = evaluar_kpis(base)
    alertas = detectar_alertas(base, estados)
    riesgos = evaluar_riesgos(base, estados)
    madurez = evaluar_madurez(base, estados)
    portafolio = evaluar_portafolio(base)
    priorizacion = priorizar_proyectos(base, riesgos, presupuesto)
    return Analisis(base.periodo_actual, estados, alertas, riesgos, riesgos_emergentes(estados, riesgos), madurez,
                    evaluar_principios(base, estados), portafolio, priorizacion,
                    indice_salud(estados, riesgos, madurez, portafolio),
                    recomendar_acciones(base, alertas, riesgos, madurez, portafolio, priorizacion))
