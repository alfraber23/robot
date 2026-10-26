"""Diagnóstico v2: extrae quién trabajó el alta desde la FIRMA del cuerpo.

Reglas:
- La firma típica tiene el nombre 1 línea ARRIBA del cargo:
    "Analista de Cuentas" | "Supervisor de Análisis de Cuentas" | "Análisis de Cuentas"
- Se ignora el header 'From' porque el buzón es compartido.
"""
import logging
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

from configuracion import Config
from correo import ProveedorCorreoOutlook
from excepciones import ErrorCorreo
from registro import configurar_logging


OPERADORES = [
    ("Alberto", ["ALBERTO"]),
    ("Angela",  ["ANGELA"]),
    ("Mariana", ["MARIANA"]),
    ("Karina",  ["KARINA"]),
    ("Erick",   ["ERICK"]),
    ("Antonio", ["ANTONIO"]),
    ("Paty",    ["PATY", "PATRICIA"]),
    ("Angy",    ["ANGY", "ANGELES JUAREZ"]),
    ("Denisse",    ["DENISSE", "CEBALLOS"]),
    
]

CARGOS_ESPECIFICOS = ["Supervisor de Análisis de Cuentas", "Analista de Cuentas", "Supervisor Análisis de Cuentas", "Supervisora de Análisis de Cuentas"]
CARGOS_GENERICOS = ["Análisis de Cuentas"]
TITULOS = ("LIC.", "ING.", "DR.", "DRA.", "MTRO.", "MTRA.", "SR.", "SRA.")


def normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.upper().split())


def limpiar_nombre(nombre: str) -> str:
    n = nombre.strip()
    for t in TITULOS:
        if n.upper().startswith(t + " "):
            return n[len(t):].strip()
    return n


def extraer_firma(cuerpo: str):
    """Devuelve (nombre_firma, cargo) o (None, None)."""
    lineas = [l.strip() for l in (cuerpo or "").splitlines() if l.strip()]

    # 1) Cargos específicos primero
    for cargo in CARGOS_ESPECIFICOS:
        for i, linea in enumerate(lineas):
            if linea == cargo and i > 0:
                return limpiar_nombre(lineas[i - 1]), cargo

    # 2) Cargo genérico (pero descartar líneas obvias como "Tel:", URLs)
    for i, linea in enumerate(lineas):
        if linea == CARGOS_GENERICOS[0] and i > 0:
            candidato = limpiar_nombre(lineas[i - 1])
            if candidato.upper().startswith(("TEL", "<", "HTTP")):
                continue
            return candidato, CARGOS_GENERICOS[0]

    return None, None


def identificar_operador(nombre_firma: str):
    if not nombre_firma:
        return None, None
    n = normalizar(nombre_firma)
    for canonico, alias in OPERADORES:
        for a in alias:
            if a in n:
                return canonico, a
    return None, None


def main():
    configurar_logging(Path("logs"))
    logging.getLogger("MAIL").setLevel(logging.WARNING)
    config = Config.cargar()

    print("=" * 78)
    print("DIAGNÓSTICO v2 — Operador desde la FIRMA (no del header From)")
    print("=" * 78)

    prov = ProveedorCorreoOutlook(config)
    try:
        prov.conectar()
    except ErrorCorreo as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    try:
        hasta = date.today()
        desde = hasta - timedelta(days=config.dias_busqueda_correo)
        correos = prov.buscar_altas(desde, hasta)
        print(f"\nAltas detectadas: {len(correos)}\n")

        estadisticas = {"ok": 0, "no_id": 0, "sin_firma": 0}

        for i, c in enumerate(correos, start=1):
            nombre, cargo = extraer_firma(c.cuerpo_texto)
            operador, alias = identificar_operador(nombre)

            print(f"[{i}/{len(correos)}] {c.enviado:%d.%m.%Y %H:%M} | {c.asunto[:70]}")
            print(f"    FROM:   {c.remitente_email}")

            if nombre:
                print(f"    FIRMA:  {nombre}  ({cargo})")
                if operador:
                    print(f"    OPERADOR: ✓ {operador}  [alias '{alias}']")
                    estadisticas["ok"] += 1
                else:
                    print(f"    OPERADOR: ⚠ NO IDENTIFICADO")
                    estadisticas["no_id"] += 1
            else:
                print(f"    FIRMA:  ⚠ no se encontró bloque de firma")
                estadisticas["sin_firma"] += 1

        print("\n" + "=" * 78)
        print(f"RESUMEN: OK={estadisticas['ok']} | "
              f"No identificado={estadisticas['no_id']} | "
              f"Sin firma={estadisticas['sin_firma']}")
    finally:
        prov.desconectar()
        print("=" * 78)
        input("Enter para salir...")


if __name__ == "__main__":
    main()