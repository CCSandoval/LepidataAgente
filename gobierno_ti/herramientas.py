import re
import unicodedata
from collections import Counter

from .datos import BaseConocimiento
from .motor import INSTANCIA_GENERAL, INSTANCIA_POR_PROCESO, ORDEN_SEVERIDAD, PLAZO_POR_SEVERIDAD, analizar, simular_escenario


def _herramienta(nombre: str, descripcion: str, requeridos: tuple = (), **parametros) -> dict:
    return {"type": "function", "function": {"name": nombre, "description": descripcion, "parameters": {
        "type": "object", "properties": parametros, "required": list(requeridos)}}}


ESQUEMAS = [
    _herramienta("evaluar_estado_ti", "Evalúa el estado general del gobierno de TI: índice de salud (0-100), semáforo de KPIs, riesgos fuera del apetito, madurez COBIT por dominio y puntaje de los principios ISO 38500. Úsala para preguntas generales o de resumen."),
    _herramienta("consultar_indicadores", "Lista los KPIs con su valor actual, meta, semáforo y tendencia. Permite filtrar por color del semáforo o por proceso COBIT.",
                 estado={"type": "string", "enum": ["todos", "rojo", "amarillo", "verde"], "description": "Filtro por semáforo"},
                 proceso={"type": "string", "description": "Código de proceso COBIT, por ejemplo DSS05"}),
    _herramienta("analizar_indicador", "Análisis detallado de un KPI: histórico, tendencia, proyección del próximo mes, alertas, riesgos que lo usan como KRI y acciones sugeridas.", ("kpi",),
                 kpi={"type": "string", "description": "Código del KPI (K01-K18) o su nombre, por ejemplo 'latencia' o 'precisión'"}),
    _herramienta("detectar_alertas", "Ejecuta el ciclo de monitoreo y devuelve las alertas tempranas (incumplimientos, deterioro sostenido, proyecciones de incumplimiento, variaciones atípicas) ordenadas por severidad.",
                 severidad_minima={"type": "string", "enum": ["baja", "media", "alta", "crítica"], "description": "Severidad mínima a reportar"}),
    _herramienta("analizar_riesgos", "Registro de riesgos de TI e IA con riesgo inherente, residual y residual ajustado dinámicamente según sus KRI, nivel, apetito, controles actuales, observaciones y riesgos emergentes.",
                 solo_fuera_apetito={"type": "boolean", "description": "true para ver solo los riesgos que superan el apetito"},
                 categoria={"type": "string", "description": "Opcional: solo si el usuario pide una categoría, por ejemplo 'Ciberseguridad' o 'Calidad de IA'"}),
    _herramienta("evaluar_madurez_cobit", "Niveles de capacidad COBIT 2019 de todos los procesos priorizados (dominios EDM, APO, BAI, DSS y MEA): nivel del diagnóstico, actual y objetivo, brecha y KPIs asociados."),
    _herramienta("priorizar_proyectos", "Prioriza los proyectos propuestos con la matriz ponderada (alineación, reducción de riesgo, valor, urgencia regulatoria y facilidad) y selecciona cuáles financiar con el presupuesto. Incluye el desempeño (SPI/CPI) de los proyectos en ejecución.",
                 presupuesto_millones={"type": "number", "description": "Presupuesto disponible en millones de COP (por defecto el de 2027)"}),
    _herramienta("simular_escenario", "Simula cómo cambia la exposición al riesgo si se ejecutan ciertos proyectos (análisis de escenarios 'qué pasa si').",
                 proyectos={"type": "array", "items": {"type": "string"}, "description": "Códigos de proyectos, por ejemplo ['P01', 'P06']. Vacío = portafolio recomendado"},
                 sumar_portafolio_recomendado={"type": "boolean", "description": "true si el usuario dice 'además', 'adicional' o 'junto con lo recomendado': suma los proyectos al portafolio recomendado"}),
    _herramienta("recomendar_acciones", "Plan de acción priorizado para la Junta y los comités (fase Dirigir de ISO 38500), con responsable, instancia de decisión, plazo, proceso COBIT y principio ISO 38500.",
                 cantidad={"type": "integer", "description": "Número de acciones a devolver (por defecto 5)"}),
    _herramienta("consultar_diagnostico", "Perfil de la empresa (misión, visión, modelo de negocio, sistemas críticos), objetivos estratégicos, hallazgos del diagnóstico con su estado de avance y modelo de gobierno de TI propuesto (estructuras con su rol y frecuencia, marcos y políticas)."),
    _herramienta("consultar_registro", "Lee el registro completo almacenado de un elemento de la compañía por su código: hallazgo (H1-H9) con descripción y evidencia, riesgo (R01-R11) con causa, consecuencia y controles, proyecto (P01-P11) con presupuesto y avance, KPI (K01-K18) con fórmula e histórico de 12 meses, proceso COBIT (por ejemplo APO12) u objetivo estratégico (OE1-OE5).", ("codigo",),
                 codigo={"type": "string", "description": "Código del elemento, por ejemplo R03, H6, P06, K05, APO12 u OE1"}),
]
PARAMETROS = {e["function"]["name"]: e["function"]["parameters"]["properties"] for e in ESQUEMAS}


def normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def resolver_kpi(base: BaseConocimiento, texto: str) -> str | None:
    m = re.search(r"\bK\s?(\d{1,2})\b", texto.upper())
    if m and f"K{int(m.group(1)):02d}" in base.kpis:
        return f"K{int(m.group(1)):02d}"
    t = normalizar(texto)
    coincidencias = [(len(normalizar(a)), kid) for kid, k in base.kpis.items() for a in [k["nombre"], *k.get("alias", [])]
                     if re.search(rf"\b{re.escape(normalizar(a))}\b", t)]
    return max(coincidencias, key=lambda c: c[0])[1] if coincidencias else None


def _convertir(valor, esquema: dict):
    tipo = esquema.get("type")
    if tipo == "number":
        return float(str(valor).replace(",", "")) if isinstance(valor, str) else float(valor)
    if tipo == "integer":
        return int(float(valor))
    if tipo == "boolean":
        return valor if isinstance(valor, bool) else str(valor).strip().lower() in ("true", "1", "si", "sí", "yes")
    if tipo == "array":
        if isinstance(valor, str):
            return re.findall(r"P\d{2}", valor.upper()) or [v.strip() for v in valor.split(",") if v.strip()]
        return [str(v) for v in valor]
    if "enum" in esquema:
        return next(e for e in esquema["enum"] if e.lower() == str(valor).lower())
    return str(valor)


def _kri(e) -> str:
    return f"{e.id} {e.nombre}: {e.valor:g} {e.unidad} (meta {e.meta:g}), {e.semaforo}, {e.tendencia}"


class Herramientas:
    def __init__(self, base: BaseConocimiento):
        self.base = base

    def ejecutar(self, nombre: str, argumentos: dict | None = None) -> dict:
        if nombre not in PARAMETROS:
            return {"error": f"Herramienta desconocida: {nombre}. Disponibles: {sorted(PARAMETROS)}"}
        args = {}
        for clave, valor in (argumentos or {}).items():
            if clave in PARAMETROS[nombre] and valor not in (None, ""):
                try:
                    args[clave] = _convertir(valor, PARAMETROS[nombre][clave])
                except (TypeError, ValueError, StopIteration):
                    return {"error": f"Valor inválido para '{clave}': {valor!r}"}
        try:
            return getattr(self, nombre)(**args)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def evaluar_estado_ti(self) -> dict:
        a = analizar(self.base)
        return {
            "periodo": a.periodo,
            "indice_salud": a.salud["indice"],
            "interpretacion": a.salud["interpretacion"],
            "componentes_indice_0_a_100": {k: v["puntaje"] for k, v in a.salud["componentes"].items()},
            "semaforo_kpis": dict(Counter(e.semaforo for e in a.estados.values())),
            "kpis_en_rojo": [f"{e.id} {e.nombre}: {e.valor:g} {e.unidad} (meta {e.meta:g})"
                             for e in a.estados.values() if e.semaforo == "rojo"],
            "cantidad_kpis": len(a.estados),
            "cantidad_riesgos_fuera_apetito": sum(r["fuera_apetito"] for r in a.riesgos),
            "cantidad_riesgos": len(a.riesgos),
            "riesgos_fuera_apetito": [f"{r['id']} {r['nombre']}: {r['puntaje_ajustado']:g} ({r['nivel']})"
                                      for r in a.riesgos if r["fuera_apetito"]],
            "apetito_riesgo_puntaje_maximo": self.base.apetito_riesgo,
            "madurez_por_dominio": a.madurez["dominios"],
            "cumplimiento_madurez_pct": a.madurez["cumplimiento_pct"],
            "principios_iso38500_puntaje_0_a_100": {p["principio"]: p["puntaje"] for p in a.principios},
            "proyectos_en_ejecucion": {p["id"]: p["semaforo"] for p in a.portafolio},
        }

    def consultar_indicadores(self, estado: str = "todos", proceso: str | None = None) -> dict:
        a = analizar(self.base)
        filas = [{"id": e.id, "nombre": e.nombre, "valor": e.valor, "unidad": e.unidad, "meta": e.meta,
                  "semaforo": e.semaforo, "tendencia": e.tendencia, "proceso": e.proceso}
                 for e in a.estados.values()
                 if estado in ("todos", e.semaforo) and (not proceso or e.proceso.upper() == proceso.upper())]
        return {"periodo": a.periodo, "total": len(filas), "indicadores": filas}

    def analizar_indicador(self, kpi: str) -> dict:
        kid = resolver_kpi(self.base, kpi)
        if kid is None:
            return {"error": f"No encontré un KPI que coincida con '{kpi}'.",
                    "kpis_disponibles": {k: v["nombre"] for k, v in self.base.kpis.items()}}
        a = analizar(self.base)
        e, k = a.estados[kid], self.base.kpis[kid]
        alerta = next((x for x in a.alertas if x["kpi"] == kid), None)
        riesgos = [r for r in a.riesgos if any(d["kri"] == kid for d in r["kris"])]
        relacionados = [a.estados[o] for o in sorted({d["kri"] for r in riesgos for d in r["kris"]} - {kid})]
        instancia = INSTANCIA_POR_PROCESO.get(e.proceso, INSTANCIA_GENERAL)
        plazo = PLAZO_POR_SEVERIDAD[alerta["severidad"]] if alerta else "Ciclo mensual de seguimiento"
        return {
            "id": kid, "nombre": e.nombre, "formula": k["formula"], "responsable": e.responsable,
            "instancia_de_decision": instancia,
            "proceso_cobit": f"{e.proceso} {self.base.procesos[e.proceso]['nombre']}",
            "objetivo_estrategico": f"{e.objetivo} {self.base.objetivos.get(e.objetivo, '')}",
            "valor_actual": e.valor, "unidad": e.unidad, "meta": e.meta, "umbral_alerta": e.umbral,
            "sentido": "mayor es mejor" if e.sentido == "max" else "menor es mejor",
            "semaforo": e.semaforo, "tendencia": e.tendencia,
            "periodos_consecutivos_empeorando": e.periodos_deterioro,
            "proyeccion_proximo_mes": round(e.proyeccion, 2), "semaforo_proyectado": e.semaforo_proyectado,
            "historico_6_meses": dict(list(self.base.serie(kid).items())[-6:]),
            "alerta": {"severidad": alerta["severidad"], "senales": alerta["senales"],
                       "plazo_sugerido": PLAZO_POR_SEVERIDAD[alerta["severidad"]]} if alerta else None,
            "riesgos_asociados": [f"{r['id']} {r['nombre']} ({r['nivel']}, puntaje {r['puntaje_ajustado']:g})" for r in riesgos],
            "indicadores_relacionados": [f"{o.id} {o.nombre}: {o.valor:g} {o.unidad}, {o.semaforo}, {o.tendencia}"
                                         for o in relacionados],
            "acciones_sugeridas": [{"accion": x, "responsable": e.responsable, "decide": instancia, "plazo": plazo}
                                   for x in k.get("acciones_sugeridas", [])],
        }

    def detectar_alertas(self, severidad_minima: str = "media") -> dict:
        a = analizar(self.base)
        alertas = [{c: x[c] for c in ("kpi", "nombre", "severidad", "senales", "responsable")} for x in a.alertas
                   if ORDEN_SEVERIDAD[x["severidad"]] >= ORDEN_SEVERIDAD[severidad_minima]]
        return {"periodo": a.periodo, "total": len(alertas),
                "por_severidad": dict(Counter(x["severidad"] for x in alertas)), "alertas": alertas}

    def analizar_riesgos(self, solo_fuera_apetito: bool = False, categoria: str | None = None) -> dict:
        a = analizar(self.base)
        filas = [{
            "id": r["id"], "nombre": r["nombre"], "categoria": r["categoria"], "dueno": r["dueno"],
            "estado": r["estado"], "tratamiento": r["tratamiento"], "procesos_cobit": r["procesos"],
            "controles_actuales": r["controles"] or ["ninguno"],
            "puntaje_inherente": r["puntaje_inherente"], "puntaje_residual": r["puntaje_residual"],
            "puntaje_ajustado_por_kri": r["puntaje_ajustado"], "nivel": r["nivel"],
            "kris": [_kri(a.estados[d["kri"]]) for d in r["kris"]],
            "proyectos_que_lo_mitigan": [f"{p['id']} ({p['estado']})" for p in self.base.proyectos.values()
                                         if any(m["riesgo"] == r["id"] for m in p["mitiga"])],
            "observaciones": r["observaciones"],
        } for r in a.riesgos if (r["fuera_apetito"] or not solo_fuera_apetito)
            and (not categoria or normalizar(categoria) in normalizar(r["categoria"]))]
        return {"apetito_riesgo": self.base.apetito_riesgo, "regla_ajuste": self.base.regla_ajuste_kri,
                "total": len(filas), "riesgos": filas, "riesgos_emergentes": a.emergentes}

    def evaluar_madurez_cobit(self) -> dict:
        m = analizar(self.base).madurez
        procesos = [{"id": p["id"], "nombre": p["nombre"], "diagnostico": p["nivel_diagnostico"],
                     "actual": p["nivel_actual"], "objetivo": p["nivel_objetivo"], "brecha": p["brecha"],
                     "kpis_en_verde_pct": p["kpis_en_verde_pct"]} for p in m["procesos"]]
        return {"escala": "0 Incompleto, 1 Inicial, 2 Gestionado, 3 Definido, 4 Cuantitativo, 5 Optimizado",
                "dominios": m["dominios"], "cumplimiento_pct": m["cumplimiento_pct"],
                "brechas_mayores": m["brechas_mayores"], "procesos": procesos}

    def priorizar_proyectos(self, presupuesto_millones: float | None = None) -> dict:
        a = analizar(self.base, presupuesto=presupuesto_millones)
        pr = a.priorizacion
        observaciones = []
        for p in pr["ranking"][:5]:
            if not p["financiado"]:
                diferibles = [q["id"] for q in pr["ranking"] if q["financiado"] and q["prioridad"] > p["prioridad"]]
                observaciones.append(f"{p['id']} (prioridad {p['prioridad']}) NO cabe en el presupuesto: faltan "
                                     f"{p['faltante']:g} M; mitiga {', '.join(p['riesgos_mitigados']) or 'ningún riesgo'}. "
                                     + (f"Opción: diferir {', '.join(diferibles)} o pedir adición presupuestal." if diferibles
                                        else "Opción: pedir adición presupuestal."))
        for p in a.portafolio:
            if p["semaforo"] == "rojo":
                observaciones.append(f"{p['id']} en ejecución está en ROJO (SPI {p['spi']}, CPI {p['cpi']}): atrasado y con sobrecosto.")
            elif p["semaforo"] == "amarillo":
                observaciones.append(f"{p['id']} en ejecución está en amarillo (SPI {p['spi']}, CPI {p['cpi']}).")
        return {
            "observaciones_clave": observaciones,
            "moneda": "millones COP", "presupuesto": pr["presupuesto"], "pesos": pr["pesos"],
            "ranking": [{"prioridad": p["prioridad"], "id": p["id"], "nombre": p["nombre"], "costo": p["presupuesto"],
                         "puntaje": p["puntaje"], "riesgos_mitigados": p["riesgos_mitigados"],
                         "financiado": p["financiado"], "faltante": p["faltante"]} for p in pr["ranking"]],
            "seleccionados": pr["seleccionados"], "total_seleccionado": pr["total_seleccionado"], "saldo": pr["saldo"],
            "proyectos_en_ejecucion": [{c: p[c] for c in ("id", "nombre", "spi", "cpi", "semaforo")} for p in a.portafolio],
        }

    def simular_escenario(self, proyectos: list[str] | None = None, sumar_portafolio_recomendado: bool = False) -> dict:
        a = analizar(self.base)
        ids = [p.upper() for p in (proyectos or [])]
        if sumar_portafolio_recomendado or not ids:
            ids = a.priorizacion["seleccionados"] + [p for p in ids if p not in a.priorizacion["seleccionados"]]
        resultado = simular_escenario(self.base, a.riesgos, ids)
        resultado["instancia_de_decision"] = ("Comité de Gobierno de TI y Producto recomienda; la Junta Directiva "
                                              "aprueba el portafolio anual")
        desconocidos = [p for p in ids if p not in self.base.proyectos]
        if desconocidos:
            resultado["advertencia"] = f"Proyectos desconocidos ignorados: {desconocidos}"
        return resultado

    def recomendar_acciones(self, cantidad: int = 5) -> dict:
        acciones = analizar(self.base).acciones
        claves = ("severidad", "accion", "justificacion", "responsable", "instancia", "plazo", "proceso_cobit",
                  "principio_iso38500")
        return {"total_identificadas": len(acciones), "acciones": [{k: x[k] for k in claves} for x in acciones[:max(1, cantidad)]]}

    def consultar_diagnostico(self) -> dict:
        org, mg, diag = self.base.organizacion, self.base.modelo_gobierno, self.base.diagnostico
        return {
            "empresa": {k: org[k] for k in ("nombre", "sector", "producto", "pais", "empleados", "equipo_tecnologia",
                                            "clientes_institucionales", "usuarios_app", "especies_en_catalogo",
                                            "presupuesto_ti_millones_cop", "mision", "vision", "modelo_negocio",
                                            "sistemas_criticos")},
            "objetivos_estrategicos": self.base.objetivos,
            "fecha_diagnostico": diag["fecha"],
            "metodologia_diagnostico": diag["metodologia"],
            "hallazgos": [{"id": h["id"], "titulo": h["titulo"], "severidad": h["severidad"], "estado": h["estado"],
                           "principio_iso38500": h["principio_iso38500"], "procesos": h["procesos_cobit"],
                           "avance": h["avance"]} for h in diag["hallazgos"]],
            "marcos": mg["marcos"],
            "estructuras": mg["estructuras"],
            "politicas": mg["politicas"],
        }

    def _nombres(self, codigos: list[str]) -> list[str]:
        b = self.base
        nombres = {**{k: v["nombre"] for t in (b.procesos, b.riesgos, b.kpis, b.proyectos) for k, v in t.items()},
                   **b.objetivos, **{h["id"]: h["titulo"] for h in b.diagnostico["hallazgos"]}}
        return [f"{c} {nombres[c]}" if c in nombres else c for c in codigos]

    def consultar_registro(self, codigo: str) -> dict:
        b = self.base
        c = codigo.strip().upper()
        hallazgos = {h["id"]: h for h in b.diagnostico["hallazgos"]}
        if c in hallazgos:
            return {"tipo": "hallazgo", **hallazgos[c], "procesos_cobit": self._nombres(hallazgos[c]["procesos_cobit"])}
        if c in b.riesgos:
            a = analizar(b)
            r = next(x for x in a.riesgos if x["id"] == c)
            return {"tipo": "riesgo", **{k: r[k] for k in ("id", "nombre", "categoria", "causa", "consecuencia")},
                    "controles_actuales": r["controles"] or ["ninguno"], "tratamiento": r["tratamiento"],
                    "estado": r["estado"], "dueno": r["dueno"], "procesos_cobit": self._nombres(r["procesos"]),
                    "puntaje_inherente": r["puntaje_inherente"], "puntaje_residual": r["puntaje_residual"],
                    "puntaje_ajustado_por_kri": r["puntaje_ajustado"], "nivel": r["nivel"],
                    "fuera_del_apetito": r["fuera_apetito"], "apetito": b.apetito_riesgo,
                    "kris": [_kri(a.estados[d["kri"]]) for d in r["kris"]],
                    "proyectos_que_lo_mitigan": [f"{p['id']} {p['nombre']} ({p['estado']})" for p in b.proyectos.values()
                                                 if any(m["riesgo"] == c for m in p["mitiga"])]}
        if c in b.proyectos:
            p = b.proyectos[c]
            rank = next((x for x in analizar(b).priorizacion["ranking"] if x["id"] == c), None)
            return {"tipo": "proyecto", "id": c, "nombre": p["nombre"], "estado": p["estado"],
                    "objetivos": self._nombres(p["objetivos"]), "moneda": "millones COP", "presupuesto": p["presupuesto"],
                    "ejecutado": p["ejecutado"], "avance_real_pct": p["avance_real"], "avance_planeado_pct": p["avance_planeado"],
                    "criterios_1_a_5": {k: p[k] for k in ("alineacion", "valor", "urgencia_regulatoria", "complejidad")},
                    "riesgos_que_mitiga": [f"{n} (reduce la probabilidad en {m['reduccion_prob']})"
                                           for n, m in zip(self._nombres([m["riesgo"] for m in p["mitiga"]]), p["mitiga"])],
                    "priorizacion_2027": {k: rank[k] for k in ("prioridad", "puntaje", "financiado", "faltante")} if rank else None}
        if c in b.procesos:
            p = b.procesos[c]
            return {"tipo": "proceso_cobit", **{k: v for k, v in p.items() if k != "hallazgos"},
                    "escala": "0 Incompleto, 1 Inicial, 2 Gestionado, 3 Definido, 4 Cuantitativo, 5 Optimizado",
                    "hallazgos": self._nombres(p["hallazgos"]),
                    "kpis": [f"{k} {v['nombre']}" for k, v in b.kpis.items() if v["proceso"] == c],
                    "riesgos": [f"{k} {v['nombre']}" for k, v in b.riesgos.items() if c in v["procesos"]]}
        if c in b.objetivos:
            return {"tipo": "objetivo_estrategico", "id": c, "nombre": b.objetivos[c],
                    "kpis": [f"{k} {v['nombre']}" for k, v in b.kpis.items() if v["objetivo"] == c],
                    "proyectos": [f"{k} {v['nombre']}" for k, v in b.proyectos.items() if c in v["objetivos"]]}
        if kid := resolver_kpi(b, codigo):
            k = b.kpis[kid]
            return {"tipo": "kpi", **{x: v for x, v in k.items() if x not in ("alias", "proceso", "objetivo")},
                    "sentido": "mayor es mejor" if k["sentido"] == "max" else "menor es mejor",
                    "proceso_cobit": self._nombres([k["proceso"]])[0], "objetivo_estrategico": self._nombres([k["objetivo"]])[0],
                    "historico_12_meses": b.serie(kid).to_dict()}
        return {"error": f"No hay ningún registro con el código '{codigo}'.",
                "codigos_validos": "H1-H9, R01-R11, P01-P11, K01-K18, OE1-OE5 o procesos COBIT como APO12"}
