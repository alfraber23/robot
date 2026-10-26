"""Lista TODOS los correos con 'ALTA' en el asunto de los últimos 2 días.
Sin filtros de remitente ni destinatario. Solo lectura."""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from configuracion import Config
from correo import ProveedorCorreoOutlook
from excepciones import ErrorCorreo
from registro import configurar_logging

log = logging.getLogger("DBG")


def mostrar_correo(prov, msg, idx):
    """Imprime un correo con formato completo."""
    try:
        fecha = prov._fecha_mensaje(msg, "inbox")
        fecha_txt = fecha.strftime("%d.%m.%Y %H:%M:%S") if fecha else "?"
    except Exception:
        fecha_txt = "?"

    try:
        asunto = msg.Subject or ""
    except Exception:
        asunto = "?"

    try:
        rem = prov._direccion_remitente(msg)
    except Exception as e:
        rem = f"[ERR: {e}]"

    print(f"\n[{idx}] {fecha_txt}")
    print(f"    Asunto: {asunto}")
    print(f"    From:   {rem}")
    print(f"    Destinatarios:")
    try:
        total = msg.Recipients.Count
    except Exception:
        total = 0
    for j in range(1, total + 1):
        try:
            rec = msg.Recipients.Item(j)
            tipo_r = {1: "To", 2: "CC", 3: "BCC"}.get(getattr(rec, "Type", -1), "?")
            dest = prov._direccion_destinatario(rec)
            print(f"      {tipo_r}: {dest}")
        except Exception:
            print(f"      {j}: [ERROR]")


def procesar_carpeta(prov, buzon_nombre, tipo_carpeta, desde):
    """Recorre una carpeta y muestra los correos con 'ALTA' recientes.

    - Intenta ordenar por fecha descendente.
    - Si el orden funciona, corta cuando lleva 200 correos seguidos fuera del rango.
    - Si no funciona, itera TODO (con tope de 5000 por seguridad).
    """
    print(f"\n===== {buzon_nombre or '(actual)'} / {tipo_carpeta} =====")

    carpeta = prov._obtener_carpeta(buzon_nombre, tipo_carpeta)
    if carpeta is None:
        print(f"[!] No se encontró la carpeta.")
        return 0

    try:
        items = carpeta.Items
        total = items.Count
    except Exception as exc:
        print(f"[ERROR abriendo carpeta] {exc}")
        return 0

    print(f"Total de correos en la carpeta: {total}")

    campo = "SentOn" if "sent" in tipo_carpeta else "ReceivedTime"
    ordenado = False
    try:
        items.Sort(f"[{campo}]", True)
        ordenado = True
        print(f"Ordenado por {campo} descendente.")
    except Exception as exc:
        print(f"[!] No se pudo ordenar por {campo}: {exc}. Se itera sin orden.")

    encontrados = 0
    revisados = 0
    fuera_de_rango = 0
    tope = 5000 if not ordenado else total

    for i in range(1, min(total, tope) + 1):
        revisados += 1
        try:
            msg = items.Item(i)
        except Exception:
            continue

        try:
            fecha = prov._fecha_mensaje(msg, tipo_carpeta)
        except Exception:
            continue

        if fecha is None:
            continue

        if fecha.date() < desde:
            fuera_de_rango += 1
            if ordenado and fuera_de_rango >= 200:
                print(f"(cortando: 200 correos seguidos más viejos que {desde})")
                break
            continue
        fuera_de_rango = 0

        try:
            asunto = (msg.Subject or "").upper()
        except Exception:
            asunto = ""

        if "ALTA" not in asunto:
            continue

        encontrados += 1
        mostrar_correo(prov, msg, encontrados)

    print(f"\n>> Revisados: {revisados} | Con 'ALTA' y fecha >= {desde}: {encontrados}")
    return encontrados


def main():
    configurar_logging(Path("logs"))
    logging.getLogger("MAIL").setLevel(logging.WARNING)
    config = Config.cargar()

    print("=" * 78)
    print("DEBUG: todos los correos con 'ALTA' en asunto (últimos 2 días)")
    print(f"Buzón configurado: {config.buzon_correo or '(actual)'}")
    print(f"Carpetas: {config.carpetas_correo}")
    print("=" * 78)

    prov = ProveedorCorreoOutlook(config)
    try:
        prov.conectar()
    except ErrorCorreo as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    desde = date.today() - timedelta(days=2)
    total_general = 0

    try:
        for entrada in config.carpetas_correo:
            entrada = (entrada + ["", ""])[:2]
            buzon_nombre, tipo_carpeta = entrada[0], entrada[1]
            total_general += procesar_carpeta(prov, buzon_nombre, tipo_carpeta, desde)
    finally:
        prov.desconectar()
        print("\n" + "=" * 78)
        print(f"TOTAL con 'ALTA' en todas las carpetas: {total_general}")
        print("=" * 78)
        input("Enter para salir...")


if __name__ == "__main__":
    main()