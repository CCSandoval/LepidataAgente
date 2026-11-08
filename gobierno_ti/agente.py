import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from .datos import BaseConocimiento
from .herramientas import ESQUEMAS, Herramientas

ARCHIVO_SECRETOS = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODELOS = [m.strip() for m in os.environ.get("AGENTE_MODELOS", "gemini-3.5-flash-lite,gemini-3.6-flash").split(",") if m.strip()]
MODELO_POR_DEFECTO = os.environ.get("AGENTE_MODELO", MODELOS[0])
MAX_PASOS = 5

INSTRUCCIONES = """Eres Monarca, el agente inteligente de apoyo al Gobierno de TI de {empresa}, empresa que desarrolla {producto}.
Asesoras a la Junta Directiva y al Comité de Gobierno de TI y Producto con base en COBIT 2019 e ISO/IEC 38500 (ciclo Evaluar-Dirigir-Monitorear).
Tus funciones: evaluar el estado de TI, monitorear indicadores, identificar riesgos y apoyar la toma de decisiones.

Reglas:
1. El perfil y el modelo de gobierno de abajo puedes usarlos directamente. Para cifras, estados, causas o controles consulta las herramientas antes de responder. Nunca inventes cifras, códigos, nombres, responsables ni plazos y, si un dato no aparece ni aquí ni en las herramientas, dilo.
2. Responde en español, con tono ejecutivo, en viñetas y en menos de 200 palabras salvo que pidan detalle.
3. En preguntas de análisis estructura la respuesta en: **Hallazgo**, **Evidencia** (cifras y códigos K/R/P y procesos COBIT) y **Recomendación** (como máximo 3 acciones, las de mayor prioridad, con quién decide y en qué plazo). Quién decide y el plazo cópialos tal cual de los campos de las herramientas (decide/instancia y plazo); no los cambies, no los repartas entre otras instancias ni pongas plazos propios. Las preguntas simples respóndelas de forma directa.
4. Usa los nombres exactos de los procesos COBIT del catálogo. Los seis principios de ISO 38500 son: Responsabilidad, Estrategia, Adquisición, Desempeño, Conformidad y Comportamiento humano; no uses otros.
5. Si la pregunta no trata del gobierno de TI de la empresa, indícalo brevemente y ofrece ayuda dentro de tu alcance.

Guía de herramientas:
- Resumen, estado general o principios de ISO 38500: evaluar_estado_ti.
- Un indicador concreto: analizar_indicador. Varios indicadores: consultar_indicadores.
- Alertas o monitoreo: detectar_alertas.
- Riesgos: analizar_riesgos (sin filtro de categoría salvo que el usuario la pida; para "los más críticos" usa solo_fuera_apetito=true).
- Qué hacer, qué decidir o recomendaciones: recomendar_acciones (trae responsable, instancia que decide y plazo).
- Inversiones o proyectos: priorizar_proyectos. "Qué pasa si" con proyectos: simular_escenario (con sumar_portafolio_recomendado=true si dicen "además" o "junto con lo recomendado").
- Madurez o brechas COBIT: evaluar_madurez_cobit.
- Diagnóstico, hallazgos, misión o visión: consultar_diagnostico.
- Detalle de un hallazgo, riesgo, proyecto, KPI, proceso u objetivo (causas, controles, evidencia, fórmula, histórico): consultar_registro.

Perfil y modelo de gobierno de la compañía:
{perfil}

Instantánea al corte {periodo} (resumen; usa las herramientas para el detalle):
{instantanea}

Catálogo de códigos:
{catalogo}"""


def clave_gemini() -> str | None:
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave and ARCHIVO_SECRETOS.exists():
        with ARCHIVO_SECRETOS.open("rb") as f:
            clave = tomllib.load(f).get("GEMINI_API_KEY")
    return clave or None


@dataclass
class Respuesta:
    texto: str
    herramientas: list[dict] = field(default_factory=list)
    error: bool = False


class AgenteGobiernoTI:
    def __init__(self, base: BaseConocimiento, modelo: str = MODELO_POR_DEFECTO):
        self.base = base
        self.herramientas = Herramientas(base)
        self.modelo = modelo
        clave = clave_gemini()
        self.cliente = OpenAI(api_key=clave, base_url=URL_GEMINI, timeout=120, max_retries=2) if clave else None

    @property
    def disponible(self) -> bool:
        return self.cliente is not None

    def _chat(self, mensajes: list[dict], herramientas: bool = True, forzar_texto: bool = False):
        if not self.cliente:
            raise RuntimeError("falta la clave GEMINI_API_KEY")
        extra = {"tools": ESQUEMAS, "tool_choice": "none" if forzar_texto else "auto"} if herramientas else {}
        return self.cliente.chat.completions.create(model=self.modelo, messages=mensajes, reasoning_effort="low",
                                                    **extra).choices[0].message

    def responder(self, pregunta: str, historial: list[dict] | None = None) -> Respuesta:
        try:
            return self._responder_llm(pregunta, historial or [])
        except Exception as exc:
            return Respuesta(_mensaje_error(exc), error=True)

    def _responder_llm(self, pregunta: str, historial: list[dict]) -> Respuesta:
        mensajes = [{"role": "system", "content": self.instrucciones()},
                    *({"role": m["role"], "content": m["content"]} for m in historial[-6:]
                      if m.get("role") in ("user", "assistant")),
                    {"role": "user", "content": pregunta}]
        usadas, vistas = [], set()
        for _ in range(MAX_PASOS):
            msg = self._chat(mensajes)
            llamadas = [(c.id, c.function.name, json.loads(c.function.arguments or "{}")) for c in msg.tool_calls or []]
            if not llamadas:
                return Respuesta(_limpiar(msg.content), usadas)
            claves = [json.dumps([n, a], sort_keys=True, default=str) for _, n, a in llamadas]
            if all(c in vistas for c in claves):
                break
            mensajes.append(msg.model_dump(exclude_none=True))
            for (id_llamada, nombre, args), clave in zip(llamadas, claves):
                resultado = self.herramientas.ejecutar(nombre, args)
                if clave not in vistas:
                    vistas.add(clave)
                    usadas.append({"nombre": nombre, "argumentos": args, "resultado": resultado})
                mensajes.append({"role": "tool", "tool_call_id": id_llamada,
                                 "content": json.dumps(resultado, ensure_ascii=False, default=str)})
        mensajes.append({"role": "user", "content": "Con la información obtenida, redacta ya la respuesta final."})
        return Respuesta(_limpiar(self._chat(mensajes, forzar_texto=True).content), usadas)

    def redactar_resumen_ejecutivo(self, hechos: str) -> str:
        return _limpiar(self._chat([
            {"role": "system", "content": "Eres un asesor de gobierno de TI. Redacta en español, en un solo párrafo de "
                                          "máximo 130 palabras, un resumen ejecutivo para la Junta Directiva. Usa solo "
                                          "los hechos entregados, cita códigos y cierra con las decisiones que se requieren."},
            {"role": "user", "content": hechos}], herramientas=False).content)

    def instrucciones(self) -> str:
        estado = self.herramientas.evaluar_estado_ti()
        b, o = self.base, self.base.organizacion
        miles = lambda n: f"{n:,}".replace(",", ".")
        perfil = "\n".join([
            f"- {o['sector']}, {o['pais']}. {o['empleados']} empleados ({o['equipo_tecnologia']} en tecnología), "
            f"{o['clientes_institucionales']} clientes institucionales, {miles(o['usuarios_app'])} usuarios de la app, "
            f"{miles(o['especies_en_catalogo'])} especies en el catálogo, presupuesto de TI de {miles(o['presupuesto_ti_millones_cop'])} M COP.",
            f"- Modelo de negocio: {o['modelo_negocio']}",
            *[f"- {e['nombre']} ({e['frecuencia']}): {e['rol']}" for e in b.modelo_gobierno["estructuras"]],
            "- Políticas: " + "; ".join(b.modelo_gobierno["politicas"]),
        ])
        instantanea = "\n".join([
            f"- Índice de salud del gobierno de TI: {estado['indice_salud']}/100 ({estado['interpretacion']})",
            f"- Semáforo de KPIs: {estado['semaforo_kpis']}",
            f"- KPIs en rojo: {'; '.join(estado['kpis_en_rojo']) or 'ninguno'}",
            f"- {estado['cantidad_riesgos_fuera_apetito']} riesgos superan el apetito (puntaje máximo "
            f"{estado['apetito_riesgo_puntaje_maximo']}): {'; '.join(estado['riesgos_fuera_apetito']) or 'ninguno'}",
            f"- Cumplimiento de madurez COBIT: {estado['cumplimiento_madurez_pct']}%",
        ])
        catalogo = "\n".join([
            "Objetivos: " + "; ".join(f"{k} {v}" for k, v in b.objetivos.items()),
            "KPIs: " + "; ".join(f"{k} {v['nombre']}" for k, v in b.kpis.items()),
            "Riesgos: " + "; ".join(f"{k} {v['nombre']}" for k, v in b.riesgos.items()),
            "Proyectos: " + "; ".join(f"{k} {v['nombre']} ({v['estado']})" for k, v in b.proyectos.items()),
            "Procesos COBIT: " + "; ".join(f"{k} {v['nombre']}" for k, v in b.procesos.items()),
        ])
        return INSTRUCCIONES.format(empresa=b.organizacion["nombre"], producto=b.organizacion["producto"],
                                    periodo=b.periodo_actual, perfil=perfil, instantanea=instantanea, catalogo=catalogo)



def _mensaje_error(exc: Exception) -> str:
    causa = {401: "la clave no es válida", 403: "la clave no tiene permiso para este modelo",
             402: "el proyecto de la clave no está en el nivel gratuito o se agotaron sus créditos (revísalo en AI Studio)",
             429: "se alcanzó el límite gratuito; espera un minuto o elige el otro modelo en la barra lateral"}.get(
        getattr(exc, "status_code", None), f"{type(exc).__name__}: {exc}")
    return f"No pude consultar Gemini: {causa}."


def _limpiar(texto: str | None) -> str:
    return (texto or "").strip() or "No pude generar una respuesta. Intenta reformular la pregunta."
