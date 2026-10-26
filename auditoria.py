"""Auditoría financiera local: el estado de cuenta debe cuadrar con el visado."""
import logging
import re
from typing import Callable

log = logging.getLogger("AUDIT")


class AuditoriaFinanciera:
    _VISADO = re.compile(r"CON DESCUENTO APLICADO.*?\)\s*([\d,]+\.\d{2})", re.IGNORECASE | re.DOTALL)
    _MONTO = re.compile(r"[\d,]+\.\d{2}")

    def __init__(self, confirmar_forzado: Callable[[str], bool], tolerancia: float = 2.0):
        """confirmar_forzado(mensaje) -> bool: pregunta al operador si fuerza la subida."""
        self._confirmar = confirmar_forzado
        self.tolerancia = tolerancia

    @staticmethod
    def limpiar_monto(texto: str) -> float:
        if not texto:
            return 0.0
        limpio = texto.replace(",", "").replace(" ", "").replace("-", "")
        try:
            return float(limpio)
        except ValueError:
            return 0.0

    def monto_visado(self, texto: str) -> float:
        coincidencia = self._VISADO.search(texto)
        return self.limpiar_monto(coincidencia.group(1)) if coincidencia else 0.0

    def monto_estado_cuenta(self, texto: str) -> float:
        partes = re.split(r"Total a Pagar:", texto, flags=re.IGNORECASE)
        if len(partes) > 1:
            numeros = self._MONTO.findall(partes[-1])
            if len(numeros) >= 3:
                return self.limpiar_monto(numeros[2])
        return 0.0

    def validar_cuadre(self, texto_edo_cuenta: str, texto_visado: str) -> bool:
        """True si se puede subir el visado (cuadra o el operador lo autorizó)."""
        monto_visado = self.monto_visado(texto_visado)
        monto_edo = self.monto_estado_cuenta(texto_edo_cuenta)
        diferencia = abs(monto_visado - monto_edo)
        log.info("Visado: $%.2f | Edo. Cuenta: $%.2f | Diferencia: $%.2f",
                 monto_visado, monto_edo, diferencia)

        if monto_visado <= 0 or monto_edo <= 0:
            motivo = (f"No se pudo leer alguno de los montos "
                      f"(Visado: ${monto_visado:,.2f} | Edo. Cuenta: ${monto_edo:,.2f}).")
        elif diferencia <= self.tolerancia:
            log.info("OK: los montos cuadran.")
            return True
        else:
            motivo = (f"Diferencia de ${diferencia:,.2f} entre Visado (${monto_visado:,.2f}) "
                      f"y Edo. Cuenta (${monto_edo:,.2f}).")

        log.warning("ALERTA: %s", motivo)
        if self._confirmar(f"{motivo}\n\n¿Deseas FORZAR la subida de este visado?"):
            log.info("Visado autorizado manualmente.")
            return True
        log.warning("Visado rechazado por el operador. Se omitirá.")
        return False
