import json
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


def _json(nombre: str) -> dict:
    return json.loads((DATA / nombre).read_text(encoding="utf-8"))


class BaseConocimiento:
    def __init__(self):
        org = _json("organizacion.json")
        self.organizacion = org["organizacion"]
        self.objetivos = {o["id"]: o["nombre"] for o in org["objetivos_estrategicos"]}
        self.diagnostico = org["diagnostico"]
        self.modelo_gobierno = org["modelo_gobierno"]
        self.procesos = {p["id"]: p for p in _json("procesos.json")["procesos"]}
        self.kpis = {k["id"]: k for k in _json("kpis.json")["kpis"]}
        riesgos = _json("riesgos.json")
        self.riesgos = {r["id"]: r for r in riesgos["riesgos"]}
        self.niveles_riesgo = riesgos["escala"]["niveles"]
        self.apetito_riesgo = riesgos["apetito_riesgo"]
        self.regla_ajuste_kri = riesgos["regla_ajuste_kri"]
        portafolio = _json("proyectos.json")
        self.proyectos = {p["id"]: p for p in portafolio["proyectos"]}
        self.presupuesto_disponible = portafolio["presupuesto_disponible_2027"]
        self.pesos_priorizacion = portafolio["criterios_priorizacion"]["pesos"]
        self.mediciones = pd.read_csv(DATA / "mediciones_kpi.csv", dtype={"kpi_id": str, "periodo": str})
        self.periodo_actual = str(self.mediciones["periodo"].max())

    def serie(self, kpi_id: str) -> pd.Series:
        df = self.mediciones[self.mediciones["kpi_id"] == kpi_id].sort_values("periodo")
        return pd.Series(df["valor"].astype(float).values, index=df["periodo"].values, name=kpi_id)
