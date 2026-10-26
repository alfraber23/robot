"""Localiza las carpetas de trabajo del día en la red."""
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional

from configuracion import Config
from excepciones import ErrorCarpeta

log = logging.getLogger("DIR")


def parsear_fecha(texto: str) -> date:
    """Acepta dd.mm.aaaa (también con / o -)."""
    limpio = texto.strip().replace("/", ".").replace("-", ".")
    try:
        return datetime.strptime(limpio, "%d.%m.%Y").date()
    except ValueError as exc:
        raise ErrorCarpeta(f"Fecha inválida '{texto}'. Usa el formato dd.mm.aaaa (ej. 17.10.2026).") from exc


class ResolutorCarpetas:
    """ALTAS \\ ALTAS <año> \\ <mes>. <NOMBRE> <año> \\ <dd.mm.aaaa>  (+ carpeta 'digitaliza')

    No arma los nombres exactos: lista las carpetas y elige la que corresponde
    (mes sin cero o con cero, cualquier nombre de mes), así tolera pequeñas variaciones.
    """

    def __init__(self, config: Config):
        self.cfg = config

    @staticmethod
    def _subcarpeta(padre: Path, predicado: Callable[[str], bool], descripcion: str) -> Path:
        try:
            coincidencias = sorted(p for p in padre.iterdir() if p.is_dir() and predicado(p.name))
        except OSError as exc:
            raise ErrorCarpeta(f"No se pudo leer '{padre}' ({exc}). ¿Hay conexión con la red y permisos?") from exc
        if not coincidencias:
            raise ErrorCarpeta(f"No existe {descripcion} dentro de '{padre}'.")
        if len(coincidencias) > 1:
            log.warning("Hay %d carpetas que coinciden con %s en '%s'. Se usa '%s'.",
                        len(coincidencias), descripcion, padre, coincidencias[0].name)
        return coincidencias[0]

    def carpeta_del_dia(self, fecha: date) -> Path:
        base = Path(self.cfg.carpeta_altas)
        anio = self._subcarpeta(
            base, lambda n: n.upper().startswith("ALTAS") and str(fecha.year) in n,
            f"la carpeta 'ALTAS {fecha.year}'")
        mes = self._subcarpeta(
            anio, lambda n: re.match(rf"^\s*0*{fecha.month}\s*\.", n) is not None,
            f"la carpeta del mes {fecha.month}")
        dia = self._subcarpeta(
            mes, lambda n: n.strip().startswith(f"{fecha:%d.%m.%Y}"),
            f"la carpeta del día {fecha:%d.%m.%Y}")
        log.info("Carpeta del día: %s", dia)
        return dia

    def _carpeta_digitaliza(self, dia: Path) -> Optional[Path]:
        if self.cfg.carpeta_digitaliza:
            ruta = Path(self.cfg.carpeta_digitaliza)
            if ruta.is_dir():
                return ruta
            log.warning("La carpeta 'digitaliza' configurada no existe o no es accesible: %s", ruta)
            return None
        for padre in (dia, Path(self.cfg.carpeta_altas)):
            try:
                for p in padre.iterdir():
                    if p.is_dir() and p.name.strip().lower() == "digitaliza":
                        return p
            except OSError as exc:
                log.debug("No se pudo buscar 'digitaliza' en %s: %s", padre, exc)
        return None

    def carpetas_para(self, fecha: date) -> List[Path]:
        """Carpeta del día + carpeta 'digitaliza' (si existe)."""
        dia = self.carpeta_del_dia(fecha)
        carpetas = [dia]
        digitaliza = self._carpeta_digitaliza(dia)
        if digitaliza is not None and digitaliza != dia:
            log.info("Carpeta 'digitaliza': %s", digitaliza)
            carpetas.append(digitaliza)
        elif digitaliza is None:
            log.info("No se encontró una carpeta 'digitaliza'. Si existe en otra ruta, "
                     "defínela en config.json con 'carpeta_digitaliza'.")
        return carpetas
