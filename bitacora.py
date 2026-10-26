"""Bitácora de Excel: abrir, registrar pacientes, guardar (con respaldo si falla)."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import openpyxl

from excepciones import ErrorBitacora
from modelos import RegistroBitacora

log = logging.getLogger("XLS")


class Bitacora:
    FILA_INICIAL = 4          # primera fila de datos
    COL_NO, COL_EPISODIO = 1, 2

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self._libro = None
        self._hoja = None

    @property
    def disponible(self) -> bool:
        return self._hoja is not None

    def existe(self) -> bool:
        return self.ruta.exists()

    def abrir(self) -> None:
        """Abre el Excel y prueba escribir YA, para fallar antes de empezar el proceso."""
        try:
            self._libro = openpyxl.load_workbook(self.ruta)
            self._hoja = self._libro.active
            self._libro.save(self.ruta)
        except PermissionError as exc:
            self._libro = self._hoja = None
            raise ErrorBitacora(
                "Cierra el archivo de Bitácora (está abierto en otro programa) y vuelve a intentar."
            ) from exc
        except Exception as exc:
            self._libro = self._hoja = None
            raise ErrorBitacora(f"No se pudo abrir la bitácora '{self.ruta.name}': {exc}") from exc
        log.info("Bitácora '%s' abierta y con permisos de escritura.", self.ruta.name)

    def _buscar(self, episodio: str) -> Tuple[bool, int]:
        """(¿ya existe el episodio?, primera fila libre)."""
        episodio = str(episodio).strip()
        fila = self.FILA_INICIAL
        while self._hoja.cell(row=fila, column=self.COL_EPISODIO).value is not None:
            if str(self._hoja.cell(row=fila, column=self.COL_EPISODIO).value).strip() == episodio:
                return True, fila
            fila += 1
        return False, fila

    def contiene(self, episodio: str) -> bool:
        return self.disponible and self._buscar(episodio)[0]

    def registrar(self, registro: RegistroBitacora) -> bool:
        """Agrega la fila en memoria. False si el episodio ya estaba registrado."""
        duplicado, fila = self._buscar(registro.episodio)
        if duplicado:
            log.info("El episodio %s ya está registrado. Ignorando...", registro.episodio)
            return False

        anterior = self._hoja.cell(row=fila - 1, column=self.COL_NO).value
        try:
            numero = int(anterior) + 1
        except (TypeError, ValueError):
            numero = 1

        self._hoja.cell(row=fila, column=self.COL_NO).value = numero
        for columna, valor in enumerate(registro.valores(), start=2):
            self._hoja.cell(row=fila, column=columna).value = valor
        log.info("Fila %d preparada en memoria para el episodio %s.", fila, registro.episodio)
        return True

    def guardar(self) -> bool:
        """Guarda en disco. Devuelve False (sin tronar) si no se pudo."""
        if not self.disponible:
            return False
        try:
            self._libro.save(self.ruta)
            log.info("Bitácora guardada en disco.")
            return True
        except PermissionError:
            log.error("No se pudo guardar la bitácora: el Excel está abierto en otro programa. "
                      "Los datos siguen en memoria y se reintentará.")
        except Exception as exc:
            log.error("No se pudo guardar la bitácora: %s", exc)
        return False

    def guardar_con_respaldo(self) -> Optional[Path]:
        """Guardado final: si falla, deja una copia de respaldo para no perder el trabajo."""
        if not self.disponible or self.guardar():
            return None
        respaldo = self.ruta.with_name(
            f"{self.ruta.stem}_RESPALDO_{datetime.now():%Y%m%d_%H%M%S}{self.ruta.suffix}")
        try:
            self._libro.save(respaldo)
            log.warning("Se guardó una COPIA DE RESPALDO en: %s", respaldo)
            return respaldo
        except Exception as exc:
            log.critical("No se pudo guardar ni el respaldo (%s). Los datos de esta sesión se perdieron.", exc)
            return None
