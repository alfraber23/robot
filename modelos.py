"""Modelos de datos del dominio."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from reglas import normalizar_habitacion


class Categoria(Enum):
    PARTICULAR = "Particular"
    SEGURO = "Seguro"
    BROKER = "Broker"        # CxC (cliente PDS): se identifica, no se sube ni se captura


class Resultado(Enum):
    PROCESADO = "procesado"
    OMITIDO = "omitido"
    ERROR = "error"


@dataclass(frozen=True)
class Documento:
    """Estado de cuenta (PDF, TXT o Excel) ya leído y clasificado."""
    ruta: Path
    tipo: str                  # "pdf", "txt" o "xls"
    episodio: str
    paciente: str
    compania: str
    categoria: Categoria
    habitacion_cruda: str = "S/D"
    impreso: Optional[datetime] = None     # fecha y hora impresas en el documento (si trae)
    modificado: float = 0.0                # fecha de modificación del archivo (respaldo)

    @property
    def nombre(self) -> str:
        return self.ruta.name

    @property
    def habitacion(self) -> str:
        return normalizar_habitacion(self.habitacion_cruda)

    @property
    def tiempo(self) -> datetime:
        """Para saber cuál es el más reciente: la hora impresa o, si no hay, la del archivo."""
        return self.impreso or datetime.fromtimestamp(self.modificado)

    @property
    def es_particular(self) -> bool:
        return self.categoria is Categoria.PARTICULAR

    @property
    def es_shriners(self) -> bool:
        return "SHRINERS" in self.compania.upper()


@dataclass
class DetalleCuenta:
    """Lo que se lee del contenido completo de un estado de cuenta (solo cuando hace falta)."""
    total_a_pagar: Optional[float] = None
    con_condiciones: bool = False      # trae deducible / coaseguro del seguro (cuenta "trabajada")
    complementario: bool = False       # particular que es deducible/coaseguro de un seguro (NC, etc.)


@dataclass
class Tarea:
    """Un estado de cuenta ya elegido para procesar."""
    doc: Documento
    broker: str = ""           # p. ej. "PDS" si el episodio tiene documento de broker
    correo_alta: Optional[object] = None   # CorreoAlta correlacionado (si existe)


@dataclass
class DatosManuales:
    """Lo que el operador captura en el pop-up."""
    broker: str = "N/A"
    vobo: str = "N/A"
    hora_cajas: str = "N/A"
    respuesta_hab: str = "N/A"
    operador: str = ""


@dataclass
class RegistroBitacora:
    """Una fila completa para la bitácora."""
    episodio: str
    habitacion: str
    paciente: str
    seguro: str
    operador: str
    datos: DatosManuales
    hora_alta: str
    fecha: str = field(default_factory=lambda: datetime.now().strftime("%d/%m/%Y"))

    def valores(self) -> tuple:
        """Columnas 2 a 12 de la bitácora (SIN la columna 'No.')."""
        d = self.datos
        return (self.episodio, self.fecha, self.habitacion, self.paciente, self.seguro,
                d.broker, self.operador, d.vobo, self.hora_alta, d.hora_cajas, d.respuesta_hab)


def filas_a_tsv(registros: Iterable[RegistroBitacora]) -> str:
    """Filas separadas por tabulador, listas para pegar en Excel (columnas B a L)."""
    def limpio(valor) -> str:
        return " ".join(str(valor).split())      # sin tabs ni saltos de línea internos
    return "\n".join("\t".join(limpio(v) for v in r.valores()) for r in registros)
