"""Script de prueba y diagnóstico para la búsqueda de brokers (PDS).

Uso:
    py debug_brokers.py               # Usa la fecha de hoy
    py debug_brokers.py 26.10.2026    # Usa una fecha específica

No modifica bitácoras ni abre el navegador. Solo lee Outlook y la carpeta del día.
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from carpetas import ResolutorCarpetas, parsear_fecha
from configuracion import Config
from correo import ProveedorCorreoOutlook
from documentos import BuscadorDocumentos, LectorDocumentos
from excepciones import ErrorCarpeta, ErrorCorreo, ErrorDigitalizador
from inventario import construir_inventario
from registro import configurar_logging
from robot import RobotDigitalizador
from seleccion import resolver_expediente


def main():
    configurar_logging(Path("logs"))
    logging.getLogger("MAIL").setLevel(logging.INFO)
    log = logging.getLogger("DBG")

    config = Config.cargar()

    # Fecha a evaluar (por argumento o hoy)
    if len(sys.argv) > 1:
        try:
            fecha = parsear_fecha(sys.argv[1])
        except ErrorCarpeta as exc:
            print(f"[ERROR] {exc}")
            sys.exit(1)
    else:
        fecha = date.today()

    print("=" * 78)
    print("DIAGNÓSTICO DE BROKERS (PDS) EN CORREO")
    print(f"Fecha de las altas: {fecha.strftime('%d.%m.%Y')}")
    dias = max(1, int(getattr(config, "dias_busqueda_brokers", 7)))
    desde = fecha - timedelta(days=dias)
    hasta = fecha
    print(f"Rango de búsqueda de brokers: {desde.strftime('%d.%m.%Y')} → {hasta.strftime('%d.%m.%Y')} ({dias} días)")
    print(f"Buzón configurado: {config.buzon_correo or '(autodetección entre 4 analistas)'}")
    print("=" * 78)

    # 1) Conectar a Outlook y buscar correos de brokers
    prov = ProveedorCorreoOutlook(config)
    try:
        prov.conectar()
    except ErrorCorreo as exc:
        print(f"\n[ERROR Outlook] {exc}")
        sys.exit(1)

    correos_brokers = []
    try:
        correos_brokers = prov.buscar_correos_brokers(desde, hasta)
        print(f"\n[1] CORREOS DE BROKERS ENCONTRADOS ({len(correos_brokers)}):")
        if not correos_brokers:
            print("    (ningún correo de broker encontrado en la última semana)")
        for i, c in enumerate(correos_brokers, start=1):
            print(f"\n  #{i} [{c.broker}] {c.enviado.strftime('%d.%m.%Y %H:%M:%S')}")
            print(f"     De:       {c.remitente}")
            print(f"     Asunto:   {c.asunto}")
            if c.adjuntos:
                print(f"     Adjuntos: {', '.join(c.adjuntos)}")
            primera_linea = next((l.strip() for l in (c.cuerpo_texto or "").splitlines() if l.strip()), "")
            if primera_linea:
                print(f"     Cuerpo:   {primera_linea[:100]}...")
    finally:
        prov.desconectar()

    # 2) Leer pacientes de la carpeta del día
    print("\n" + "=" * 78)
    print(f"[2] CRUCE CON PACIENTES DE LAS ALTAS ({fecha.strftime('%d.%m.%Y')}):")
    resolutor = ResolutorCarpetas(config)
    try:
        carpetas = resolutor.carpetas_para(fecha)
        lector = LectorDocumentos(config)
        buscador = BuscadorDocumentos(carpetas)
        candidatos = []
        for c in carpetas:
            candidatos.extend(p for p in c.iterdir() if p.is_file() and LectorDocumentos.es_candidato(p.name))
        
        if not candidatos:
            print(f"    (no hay documentos en la carpeta de altas para {fecha.strftime('%d.%m.%Y')})")
            print("=" * 78)
            input("Presiona Enter para salir...")
            return

        inv = construir_inventario(candidatos, lector, buscador)
        tareas = []
        for exp in inv.expedientes:
            res = resolver_expediente(exp, config.nombre_servicios_especializados,
                                      config.palabras_broker, lambda *a: 0)
            if res.tarea:
                tareas.append(res.tarea)

        print(f"Episodios a procesar encontrados: {len(tareas)}\n")
        con_broker = 0
        sin_broker = 0

        for idx, t in enumerate(tareas, start=1):
            doc = t.doc
            broker_detectado = RobotDigitalizador._correlacionar_broker(doc, correos_brokers)
            resultado = broker_detectado or t.broker or "N/A"
            if broker_detectado:
                con_broker += 1
                icono = "✓ BROKER DETECTADO:"
            elif t.broker:
                con_broker += 1
                icono = "✓ BROKER POR ARCHIVO:"
            else:
                sin_broker += 1
                icono = "• Sin broker (queda):"

            print(f"  {idx:2d}. Ep {doc.episodio} | {doc.paciente} | Cía: {doc.compania}")
            print(f"      {icono} {resultado}")

        print("\n" + "-" * 78)
        print(f"RESUMEN: Con broker asignado = {con_broker} | Sin broker (N/A) = {sin_broker}")
    except (ErrorCarpeta, ErrorDigitalizador) as exc:
        print(f"    [AVISO] No se pudo leer la carpeta de altas: {exc}")
    except Exception as exc:
        print(f"    [ERROR inesperado leyendo carpeta]: {exc}")

    print("=" * 78)
    input("Presiona Enter para salir...")


if __name__ == "__main__":
    main()
