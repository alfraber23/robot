"""Configuración del logging: todo a consola y, además, a logs/digitalizador.log."""
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s"


def configurar_logging(carpeta_logs: Path) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG)
    raiz.handlers.clear()

    # Consola (INFO): lo que el operador necesita ver
    if sys.stdout is not None:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")  # nunca truena por un caracter raro
        consola = logging.StreamHandler(sys.stdout)
        consola.setLevel(logging.INFO)
        consola.setFormatter(logging.Formatter(FORMATO, "%H:%M:%S"))
        raiz.addHandler(consola)

    # Archivo (DEBUG): detalle completo para diagnosticar después
    try:
        carpeta_logs.mkdir(exist_ok=True)
        archivo = RotatingFileHandler(
            carpeta_logs / "digitalizador.log",
            maxBytes=1_000_000, backupCount=3, encoding="utf-8",
        )
        archivo.setLevel(logging.DEBUG)
        archivo.setFormatter(logging.Formatter(FORMATO, "%Y-%m-%d %H:%M:%S"))
        raiz.addHandler(archivo)
    except OSError as exc:
        logging.getLogger("SYS").warning(
            "No se pudo crear el archivo de log (%s). Solo se usará la consola.", exc)

    # Librerías ruidosas
    for nombre in ("selenium", "urllib3"):
        logging.getLogger(nombre).setLevel(logging.WARNING)
    for nombre in ("PyPDF2", "pypdf"):
        logging.getLogger(nombre).setLevel(logging.ERROR)

    # Cualquier excepción no capturada queda registrada con su traceback
    def _hook_principal(tipo, valor, traza):
        if issubclass(tipo, KeyboardInterrupt):
            sys.__excepthook__(tipo, valor, traza)
            return
        logging.getLogger("SYS").critical("Excepción no controlada", exc_info=(tipo, valor, traza))

    def _hook_hilos(args):
        logging.getLogger("SYS").critical(
            "Excepción no controlada en el hilo '%s'",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _hook_principal
    threading.excepthook = _hook_hilos
