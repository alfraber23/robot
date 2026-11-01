"""Reglas para decidir QUÉ archivo de cada episodio se digitaliza.

Un episodio puede tener varios estados de cuenta:
  - del seguro: el inicial (se manda por correo) y el trabajado (con deducible/coaseguro);
  - particulares: nota de crédito, particular...
  - de broker: CxC (cliente PDS).
Solo se sube UNO: el estado de cuenta del seguro definitivo. Lo demás se identifica y se ignora.
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from documentos import leer_detalle
from inventario import Expediente
from modelos import DetalleCuenta, Documento, Tarea, TipoProceso
from reglas import formato_requerido

log = logging.getLogger("SEL")

# preguntar(titulo, mensaje, opciones, sugerida) -> índice elegido, o None si cancela
Preguntar = Callable[[str, str, List[str], int], Optional[int]]
# confirmar(titulo, mensaje) -> sí/no
Confirmar = Callable[[str, str], bool]
# elegir_varias(titulo, mensaje, opciones) -> índices marcados, o None
ElegirVarias = Callable[[str, str, List[str]], Optional[List[int]]]

_MARGEN_SEGUNDOS = 60


@dataclass
class ResultadoExpediente:
    tarea: Optional[Tarea] = None
    faltante: Optional[str] = None              # por qué NO se puede procesar
    ignorados: List[str] = field(default_factory=list)


def _descripcion(doc: Documento, detalle: DetalleCuenta) -> str:
    partes = [doc.nombre, f"fecha {doc.tiempo:%d.%m.%Y %H:%M:%S}"]
    if detalle.total_a_pagar is not None:
        partes.append(f"total ${detalle.total_a_pagar:,.2f}")
    if doc.tipo in ("pdf", "xls"):
        partes.append("con deducible/coaseguro" if detalle.con_condiciones else "sin deducible/coaseguro")
    return "  |  ".join(partes)


def elegir_estado_cuenta(candidatos: List[Documento], preguntar: Preguntar) -> Tuple[Documento, str]:
    """Elige el estado de cuenta definitivo del seguro. Devuelve (documento, motivo)."""
    if len(candidatos) == 1:
        return candidatos[0], "único estado de cuenta"

    infos = [(d, leer_detalle(d)) for d in candidatos]
    mas_reciente = max(infos, key=lambda p: p[0].tiempo)
    trabajados = [p for p in infos if p[1].con_condiciones]

    if trabajados:
        mejor = max(trabajados, key=lambda p: p[0].tiempo)
        motivo = "trabajado (con deducible/coaseguro) y el más reciente"
        ambiguo = mejor is not mas_reciente and mas_reciente[0].tiempo > mejor[0].tiempo
        grupo = trabajados
    else:
        mejor = mas_reciente
        motivo = "el más reciente"
        ambiguo = False
        grupo = infos

    # Otro candidato casi a la misma hora y que NO es idéntico: no se puede decidir solo
    for otro in grupo:
        if otro is mejor:
            continue
        cerca = abs((otro[0].tiempo - mejor[0].tiempo).total_seconds()) < _MARGEN_SEGUNDOS
        identico = (otro[1].total_a_pagar is not None and otro[1].total_a_pagar == mejor[1].total_a_pagar
                    and otro[1].con_condiciones == mejor[1].con_condiciones)
        if cerca and not identico:
            ambiguo = True

    if ambiguo:
        opciones = [_descripcion(d, det) for d, det in infos]
        sugerida = infos.index(mejor)
        mensaje = (f"El episodio {mejor[0].episodio} ({mejor[0].paciente}) tiene {len(infos)} estados de cuenta "
                   "del seguro y no se pudo decidir cuál es el definitivo.\nElige el que se va a digitalizar:")
        indice = preguntar("Elegir estado de cuenta", mensaje, opciones, sugerida)
        if indice is not None and 0 <= indice < len(infos):
            return infos[indice][0], "elegido por el operador"
        log.warning("Sin respuesta del operador: se usa '%s'.", mejor[0].nombre)
        motivo += " (sin confirmar)"

    return mejor[0], motivo


def preguntar_separacion(candidatos: List[Documento], confirmar: Optional[Confirmar],
                         elegir_varias: Optional[ElegirVarias]) -> List[Documento]:
    """Si hay 2+ estados de cuenta, pregunta si es separación de cuentas.

    Devuelve los 2 o 3 documentos elegidos, o [] si NO es separación.
    """
    if len(candidatos) < 2 or confirmar is None or elegir_varias is None:
        return []
    ejemplo = candidatos[0]
    if not confirmar("Separación de cuentas",
                     f"El episodio {ejemplo.episodio} ({ejemplo.paciente}) tiene {len(candidatos)} "
                     "estados de cuenta del seguro.\n\n¿Es una separación de cuentas?"):
        return []
    infos = [(d, leer_detalle(d)) for d in candidatos]
    indices = elegir_varias(
        "Estados de cuenta de la separación",
        f"Marca los estados de cuenta a digitalizar del episodio {ejemplo.episodio} "
        "(uno por carpetita roja):",
        [_descripcion(d, det) for d, det in infos])
    elegidos = [candidatos[i] for i in (indices or []) if 0 <= i < len(candidatos)]
    if len(elegidos) < 2:
        log.warning("Ep %s: se marcaron %d archivo(s); no se trata como separación.",
                    ejemplo.episodio, len(elegidos))
        return []
    return elegidos


def resolver_expediente(exp: Expediente, nombre_especializados: str, palabras_broker: List[str],
                        preguntar: Preguntar, confirmar: Optional[Confirmar] = None,
                        elegir_varias: Optional[ElegirVarias] = None) -> ResultadoExpediente:
    res = ResultadoExpediente()

    if exp.seguros:
        usables = [d for d in exp.seguros
                   if d.tipo == formato_requerido(d.compania, nombre_especializados)]
        if not usables:
            requerido = formato_requerido(exp.seguros[0].compania, nombre_especializados).upper()
            existentes = ", ".join(sorted({d.tipo.upper() for d in exp.seguros}))
            res.faltante = (f"{exp.seguros[0].paciente} (Ep {exp.episodio}, {exp.seguros[0].compania}): "
                            f"solo hay estado de cuenta en {existentes} y se necesita {requerido}.")
            return res

        separados = preguntar_separacion(usables, confirmar, elegir_varias)
        if separados:
            doc, motivo = separados[0], f"separación de cuentas ({len(separados)} estados)"
        else:
            doc, motivo = elegir_estado_cuenta(usables, preguntar)
        elegidos = separados or [doc]
        res.ignorados = ([f"{d.nombre} (estado de cuenta del seguro no elegido)" for d in exp.seguros
                          if d not in elegidos]
                         + [f"{d.nombre} (particular/complementario)" for d in exp.particulares]
                         + [f"{d.nombre} (broker: {d.compania})" for d in exp.brokers])
        broker = ""
        if exp.brokers:
            texto = exp.brokers[0].compania.upper()
            broker = next((p.upper() for p in palabras_broker if p.upper() in texto), "PDS")
        res.tarea = Tarea(doc, broker)
        if separados:
            res.tarea.tipo_proceso = TipoProceso.SEPARACION
            res.tarea.docs_separacion = separados
        log.info("Ep %s: se usa '%s' (%s).", exp.episodio, ", ".join(d.nombre for d in elegidos), motivo)
        return res

    # Sin estado de cuenta del seguro
    complementarios = [d for d in exp.particulares if leer_detalle(d).complementario]
    if exp.brokers or complementarios:
        ejemplo = (exp.brokers + exp.particulares)[0]
        res.faltante = (f"{ejemplo.paciente} (Ep {exp.episodio}): solo hay documentos complementarios "
                        "(nota de crédito / particular / CxC); falta el estado de cuenta del seguro.")
        return res

    doc = max(exp.particulares, key=lambda d: d.tiempo)
    res.ignorados = [f"{d.nombre} (particular repetido)" for d in exp.particulares if d is not doc]
    res.tarea = Tarea(doc, "")
    log.info("Ep %s: paciente particular, se usa '%s'.", exp.episodio, doc.nombre)
    return res
