"""Punto de entrada. Ejecutar con:  py main.py"""
import logging
import sys

import customtkinter as ctk

from configuracion import DIR_BASE, Config
from excepciones import ErrorConfiguracion
from registro import configurar_logging
from robot import RobotDigitalizador
from ui import VentanaPrincipal
from version import VERSION


def main() -> None:
    configurar_logging(DIR_BASE / "logs")
    log = logging.getLogger("SYS")
    log.info("Robot Digitalizador v%s | Python %s", VERSION, sys.version.split()[0])

    try:
        config = Config.cargar()
    except ErrorConfiguracion as exc:
        log.critical("%s", exc)
        sys.exit(1)

    if config.usar_carpeta_por_fecha:
        log.info("Carpeta base de altas: %s", config.carpeta_altas)
    else:
        log.info("Carpeta de documentos (modo fijo): %s", config.carpeta_documentos)
    log.info("Bitácora: %s", config.ruta_bitacora)

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    def crear_robot(operador, fecha, interaccion):
        return RobotDigitalizador(config, interaccion, operador, fecha)

    VentanaPrincipal(crear_robot).mainloop()


if __name__ == "__main__":
    main()
