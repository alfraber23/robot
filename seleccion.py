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
from modelos import DetalleCuenta, Documento, Tarea
from reglas import formato_requerido

log = logging.getLogger("SEL")

# preguntar(titulo, mensaje, opciones, sugerida) -> índice elegido, o None si cancela
Preguntar = Callable[[str, str, List[str], int], Optional[int]]

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


def resolver_expediente(exp: Expediente, nombre_especializados: str, palabras_broker: List[str],
                        preguntar: Preguntar) -> ResultadoExpediente:
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

        doc, motivo = elegir_estado_cuenta(usables, preguntar)
        res.ignorados = ([f"{d.nombre} (estado de cuenta del seguro no elegido)" for d in exp.seguros if d is not doc]
                         + [f"{d.nombre} (particular/complementario)" for d in exp.particulares]
                         + [f"{d.nombre} (broker: {d.compania})" for d in exp.brokers])
        broker = ""
        if exp.brokers:
            texto = exp.brokers[0].compania.upper()
            broker = next((p.upper() for p in palabras_broker if p.upper() in texto), "PDS")
        res.tarea = Tarea(doc, broker)
        log.info("Ep %s: se usa '%s' (%s).", exp.episodio, doc.nombre, motivo)
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
