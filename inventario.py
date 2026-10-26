"""Inventario de archivos: agrupa por episodio y separa estados de cuenta, adicionales y no reconocidos."""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from documentos import BuscadorDocumentos, LectorDocumentos
from excepciones import ErrorFormatoDocumento, ErrorLecturaDocumento, ErrorVarianteTxt
from modelos import Categoria, Documento

log = logging.getLogger("DOC")


@dataclass
class Expediente:
    """Todos los estados de cuenta de un mismo episodio."""
    episodio: str
    seguros: List[Documento] = field(default_factory=list)
    particulares: List[Documento] = field(default_factory=list)   # nota de crédito, particular...
    brokers: List[Documento] = field(default_factory=list)        # CxC (cliente PDS)


@dataclass
class Inventario:
    expedientes: List[Expediente] = field(default_factory=list)
    adicionales: List[Path] = field(default_factory=list)         # visados, resúmenes, control de costos
    no_reconocidos: List[Path] = field(default_factory=list)      # nadie los reclama: revisar
    incidencias: List[str] = field(default_factory=list)          # TXT con variante errónea, ilegibles
    total_documentos: int = 0


def construir_inventario(rutas: List[Path], lector: LectorDocumentos,
                         buscador: BuscadorDocumentos) -> Inventario:
    inv = Inventario()

    # 1) El mismo archivo en dos carpetas (mismo nombre y tamaño): se usa solo uno
    vistos: Dict[Tuple[str, int], Path] = {}
    unicas: List[Path] = []
    for ruta in rutas:
        try:
            clave = (ruta.name.lower(), ruta.stat().st_size)
        except OSError as exc:
            log.warning("No se pudo acceder a '%s': %s", ruta, exc)
            inv.incidencias.append(f"No se pudo acceder a '{ruta.name}': {exc}")
            continue
        if clave in vistos:
            log.info("'%s' está repetido (%s y %s). Se usa solo el primero.",
                     ruta.name, vistos[clave].parent.name, ruta.parent.name)
            continue
        vistos[clave] = ruta
        unicas.append(ruta)

    # 2) Lectura de cada archivo (NO se descartan "duplicados": un episodio puede tener
    #    dos estados de cuenta del seguro, el inicial y el trabajado; se elige después)
    documentos: List[Documento] = []
    pendientes: List[Path] = []
    for ruta in unicas:
        try:
            documentos.append(lector.leer(ruta))
        except ErrorVarianteTxt as exc:
            log.error("ALERTA: %s", exc)
            inv.incidencias.append(str(exc))
        except ErrorFormatoDocumento:
            pendientes.append(ruta)
        except ErrorLecturaDocumento as exc:
            log.warning("%s", exc)
            inv.incidencias.append(str(exc))
        except Exception as exc:
            log.exception("Error inesperado leyendo '%s'", ruta.name)
            inv.incidencias.append(f"Error inesperado leyendo '{ruta.name}': {exc}")
    inv.total_documentos = len(documentos)

    # 3) Agrupar por episodio
    por_episodio: Dict[str, Expediente] = {}
    for doc in documentos:
        exp = por_episodio.setdefault(doc.episodio, Expediente(doc.episodio))
        {Categoria.SEGURO: exp.seguros,
         Categoria.PARTICULAR: exp.particulares,
         Categoria.BROKER: exp.brokers}[doc.categoria].append(doc)
    inv.expedientes = list(por_episodio.values())

    # 4) El resto: ¿adicional de algún episodio, control de costos, o desconocido?
    for ruta in pendientes:
        if any(ep in ruta.name for ep in por_episodio):
            inv.adicionales.append(ruta)
        elif ruta.suffix.lower() == ".pdf" and buscador.contiene(ruta, "CONTROL DE COSTOS"):
            inv.adicionales.append(ruta)
        else:
            inv.no_reconocidos.append(ruta)

    log.info("Inventario: %d estado(s) de cuenta de %d episodio(s) | %d documento(s) adicional(es) | %d sin reconocer.",
             len(documentos), len(inv.expedientes), len(inv.adicionales), len(inv.no_reconocidos))
    return inv
