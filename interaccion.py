"""Contrato entre el robot y quien lo maneja (interfaz gráfica, consola, pruebas...).

El robot NO conoce tkinter: solo habla con esta interfaz. Así se puede cambiar
la UI o probar el robot sin ventanas.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from modelos import DatosManuales


class Interaccion(ABC):
    @abstractmethod
    def estado(self, mensaje: str, nivel: str = "ok") -> None:
        """Muestra un estado. nivel: 'ok' | 'aviso' | 'error' | 'info'."""

    def progreso(self, actual: int, total: int) -> None:
        """Actualiza el progreso determinista (actual de total)."""

    @abstractmethod
    def pedir_datos_manuales(self, paciente: str, episodio: str, modo: str,
                             broker_sugerido: str = "",
                             sugeridos: Optional[DatosManuales] = None,
                             pedir_operador: bool = False) -> DatosManuales:
        """Bloquea hasta que el operador captura los datos manuales.

        modo: 'completo' | 'particular' | 'hemo' | 'simple'.
        sugeridos: valores ya conocidos (hora del correo, etc.) que la UI
                   debe precargar en el pop-up para que el operador solo confirme.
        pedir_operador: True cuando no se pudo obtener el operador desde el correo.
        """

    @abstractmethod
    def elegir_opcion(self, titulo: str, mensaje: str, opciones: List[str], sugerida: int) -> Optional[int]:
        """Muestra opciones y devuelve el índice elegido (None si el operador cancela)."""

    @abstractmethod
    def pedir_archivo(self, titulo: str, mensaje: str, carpeta_inicial: Path) -> Optional[Path]:
        """Deja seleccionar un archivo a mano. None si el operador decide omitirlo."""

    @abstractmethod
    def confirmar(self, titulo: str, mensaje: str) -> bool:
        """Pregunta sí/no al operador y bloquea hasta que responda."""

    @abstractmethod
    def mostrar_resultado(self, tsv: str, total: int) -> None:
        """Muestra las filas nuevas (separadas por tabulador) listas para pegar en el Excel web."""

    @abstractmethod
    def terminado(self, exito: bool) -> None:
        """El robot terminó (con éxito o no); la UI debe reactivar sus controles."""
