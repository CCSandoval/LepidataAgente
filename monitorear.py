import sys
from datetime import date
from pathlib import Path

from gobierno_ti.agente import AgenteGobiernoTI
from gobierno_ti.datos import BaseConocimiento
from gobierno_ti.formato import ICONO_SEVERIDAD
from gobierno_ti.informe import generar_informe
from gobierno_ti.motor import analizar

base = BaseConocimiento()
agente = AgenteGobiernoTI(base)
if not agente.disponible:
    sys.exit("Falta la clave de Gemini: guárdala como GEMINI_API_KEY en .streamlit/secrets.toml.")

a = analizar(base)
print(f"Ciclo de monitoreo · {base.organizacion['nombre']} · corte {a.periodo}")
print(f"Índice de salud: {a.salud['indice']}/100 ({a.salud['interpretacion']})")
for x in a.alertas:
    if x["severidad"] in ("crítica", "alta"):
        print(f"  {ICONO_SEVERIDAD[x['severidad']]} {x['kpi']} {x['nombre']}: {x['senales'][0]}")
print(f"Riesgos fuera del apetito: {', '.join(r['id'] for r in a.riesgos if r['fuera_apetito']) or 'ninguno'}")

ruta = Path(__file__).parent / "informes" / f"informe_{a.periodo}_{date.today().isoformat()}.md"
ruta.parent.mkdir(exist_ok=True)
ruta.write_text(generar_informe(base, agente), encoding="utf-8")
print(f"Informe generado: {ruta}")
