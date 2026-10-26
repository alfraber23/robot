"""Diagnóstico del acceso a Outlook desde el robot.

Uso:
    py diagnostico_correo.py

No modifica nada. Solo lee y muestra.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

from configuracion import Config
from correo import ProveedorCorreoOutlook
from excepciones import ErrorCorreo
from registro import configurar_logging


def main():
    configurar_logging(Path("logs"))
    config = Config.cargar()

    print("=" * 78)
    print("DIAGNÓSTICO DE CORREO  —  Outlook de escritorio")
    print("=" * 78)

    import logging
    logging.getLogger("MAIL").setLevel(logging.DEBUG)

    prov = ProveedorCorreoOutlook(config)
    try:
        prov.conectar()
        print(f"\n[i] BUZÓN CONFIGURADO EN configuracion.py: {config.buzon_correo}")
        print(f"    Carpetas a revisar: {prov.obtener_carpetas_busqueda()}")
    except ErrorCorreo as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    try:
        # 1) Buzones
        print("\n[1] BUZONES ACCESIBLES")
        for nombre in prov.listar_buzones():
            print(f"    • {nombre}")

        # 2) Carpetas
        print("\n[2] CARPETAS DE CADA BUZÓN")
        for buzon, carpetas in prov.listar_carpetas().items():
            print(f"\n    {buzon}:")
            for c in carpetas:
                print(f"      - {c}")

        # 3) Dónde hay correos de altas
        hasta = date.today()
        desde = hasta - timedelta(days=config.dias_busqueda_correo)
        print(f"\n[3] ¿DÓNDE HAY CORREOS DE ALTAS? "
              f"({desde:%d.%m.%Y} → {hasta:%d.%m.%Y})")
        mapa = prov.contar_altas_por_carpeta(desde, hasta)
        if not mapa:
            print("    (ninguno en las carpetas accesibles)")
        for ubicacion, conteo in sorted(mapa.items(), key=lambda x: -x[1]):
            print(f"    • {conteo:4d}  {ubicacion}")

        # 4) Muestra de correos de altas (UNA sola llamada)
        print(f"\n[4] MUESTRA DE CORREOS DE ALTAS (config actual: {config.carpetas_correo})")
        correos = prov.buscar_altas(desde, hasta)
        print(f"    Encontrados: {len(correos)}")
        for c in correos[:5]:
            print(f"\n    • {c.enviado:%d.%m.%Y %H:%M:%S}  [{c.carpeta_origen}]")
            print(f"      Operador (from):  {c.remitente_nombre} <{c.remitente_email}>")
            print(f"      Para:             {', '.join(c.destinatarios)}")
            print(f"      Asunto:           {c.asunto}")
            if c.adjuntos:
                print(f"      Adjuntos:         {', '.join(c.adjuntos)}")

        # 5) Últimos 5 correos enviados (sin filtro de altas)
        print("\n[5] ÚLTIMOS 5 CORREOS ENVIADOS (sin filtro de altas)")
        try:
            carpeta = prov._obtener_carpeta("", "sent")
            if carpeta and carpeta.Items.Count > 0:
                items = carpeta.Items
                try:
                    items.Sort("[SentOn]", True)
                except Exception:
                    pass
                for i in range(1, min(items.Count, 5) + 1):
                    try:
                        msg = items.Item(i)
                        fecha = prov._fecha_mensaje(msg, "sent")
                        fecha_txt = fecha.strftime("%d.%m.%Y %H:%M") if fecha else "?"
                        de = prov._direccion_remitente(msg)
                        para = [prov._direccion_destinatario(msg.Recipients.Item(j))
                                for j in range(1, msg.Recipients.Count + 1)]
                        print(f"\n    {i}. {fecha_txt}")
                        print(f"       De:     {de}")
                        print(f"       Para:   {', '.join(p for p in para if p)[:150]}")
                        print(f"       Asunto: {msg.Subject}")
                    except Exception as exc:
                        print(f"    [{i}] Error leyendo: {exc}")
            else:
                print("    (la carpeta de enviados está vacía)")
        except Exception as exc:
            print(f"    [ERROR] {exc}")

        # 6) Últimos 5 correos recibidos
        print("\n[6] ÚLTIMOS 5 CORREOS RECIBIDOS (Bandeja de entrada)")
        try:
            carpeta = prov._obtener_carpeta("", "inbox")
            if carpeta and carpeta.Items.Count > 0:
                items = carpeta.Items
                try:
                    items.Sort("[ReceivedTime]", True)
                except Exception:
                    pass
                for i in range(1, min(items.Count, 5) + 1):
                    try:
                        msg = items.Item(i)
                        fecha = prov._fecha_mensaje(msg, "inbox")
                        fecha_txt = fecha.strftime("%d.%m.%Y %H:%M") if fecha else "?"
                        de = prov._direccion_remitente(msg)
                        para = [prov._direccion_destinatario(msg.Recipients.Item(j))
                                for j in range(1, msg.Recipients.Count + 1)]
                        print(f"\n    {i}. {fecha_txt}")
                        print(f"       De:     {de}")
                        print(f"       Para:   {', '.join(p for p in para if p)[:200]}")
                        print(f"       Asunto: {msg.Subject}")
                    except Exception as exc:
                        print(f"    [{i}] Error leyendo: {exc}")
            else:
                print("    (la bandeja de entrada está vacía o no accesible)")
        except Exception as exc:
            print(f"    [ERROR] {exc}")

    finally:
        # Desconectar SIEMPRE al final, pase lo que pase
        prov.desconectar()
        print("\n" + "=" * 78)
        input("Enter para salir...")

if __name__ == "__main__":
    main()