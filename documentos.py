"""Lectura de documentos (PDF/TXT), recorte de PDFs y búsqueda de documentos adicionales."""
import logging
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

import openpyxl

# Motores de PDF disponibles. PyPDF2 va primero (el que ya usaba el proyecto, para
# no alterar la extracción de texto); pypdf sirve de respaldo y de segunda opinión
# al buscar documentos adicionales.
_MOTORES = []
try:
    import PyPDF2 as _pypdf2
    _MOTORES.append(("PyPDF2", _pypdf2))
except ImportError:  # pragma: no cover
    pass
try:
    import pypdf as _pypdf
    _MOTORES.append(("pypdf", _pypdf))
except ImportError:  # pragma: no cover
    pass
if not _MOTORES:  # pragma: no cover
    raise ImportError("Falta una librería de PDF. Instala: py -m pip install --user PyPDF2")
PdfReader = _MOTORES[0][1].PdfReader
PdfWriter = _MOTORES[0][1].PdfWriter

from excepciones import ErrorFormatoDocumento, ErrorLecturaDocumento, ErrorVarianteTxt
from modelos import Categoria, DetalleCuenta, Documento

log = logging.getLogger("DOC")


# ----------------------------------------------------------------------
# Utilidades de PDF
# ----------------------------------------------------------------------
def leer_primera_pagina(ruta: Path) -> str:
    with open(ruta, "rb") as archivo:
        lector = PdfReader(archivo)
        return lector.pages[0].extract_text() or ""


def extraer_texto_completo(ruta: Path) -> str:
    """Texto de todas las páginas (para auditoría). Devuelve "" si falla."""
    try:
        with open(ruta, "rb") as archivo:
            lector = PdfReader(archivo)
            return "".join(f"{pagina.extract_text() or ''} \n " for pagina in lector.pages)
    except Exception as exc:
        log.error("No se pudo extraer el texto completo de '%s': %s", Path(ruta).name, exc)
        return ""


def normalizar(texto: str) -> str:
    """MAYÚSCULAS, sin acentos y con espacios colapsados."""
    sin_acentos = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.upper().split())


def compactar(texto: str) -> str:
    """Solo letras y números: tolera palabras pegadas o separadas de más en el PDF."""
    return re.sub(r"[^A-Z0-9]", "", normalizar(texto))


def textos_por_motor(ruta: Path, paginas: int = 2) -> List[str]:
    """Texto de las primeras páginas con cada motor de PDF disponible."""
    textos = []
    for nombre, modulo in _MOTORES:
        try:
            with open(ruta, "rb") as archivo:
                lector = modulo.PdfReader(archivo)
                textos.append(" ".join((p.extract_text() or "") for p in lector.pages[:paginas]))
        except Exception as exc:
            log.debug("%s no pudo leer '%s': %s", nombre, Path(ruta).name, exc)
    return textos


@contextmanager
def ultima_pagina_temporal(ruta_pdf: Path, etiqueta: str) -> Iterator[Path]:
    """Entrega una copia temporal con solo la última página (AXA) y la borra al salir.

    Si no se puede recortar, entrega el PDF original completo (con aviso).
    """
    ruta_temp = Path(tempfile.gettempdir()) / f"digitalizador_axa_{etiqueta}.pdf"
    creado = False
    try:
        with open(ruta_pdf, "rb") as entrada:
            lector = PdfReader(entrada)
            escritor = PdfWriter()
            escritor.add_page(lector.pages[-1])
            with open(ruta_temp, "wb") as salida:
                escritor.write(salida)
        creado = True
        resultado = ruta_temp
        log.info("PDF de AXA recortado a la última hoja.")
    except Exception as exc:
        log.warning("No se pudo recortar el PDF de AXA (%s). Se usará el PDF completo.", exc)
        resultado = Path(ruta_pdf)

    try:
        yield resultado
    finally:
        if creado:
            try:
                ruta_temp.unlink()
            except OSError:
                # Windows: Chrome aún tiene el archivo abierto. Se borra al terminar (limpiar_temporales).
                log.debug("Temporal '%s' en uso; se borrará al terminar.", ruta_temp.name)


def limpiar_temporales() -> None:
    """Borra los temporales del robot (llamar cuando Chrome ya se cerró)."""
    for ruta in Path(tempfile.gettempdir()).glob("digitalizador_axa_*.pdf"):
        try:
            ruta.unlink()
        except OSError as exc:
            log.warning("No se pudo borrar el temporal '%s': %s", ruta, exc)


# ----------------------------------------------------------------------
# Excel (servicios especializados)
# ----------------------------------------------------------------------
_COLUMNAS_EXCEL = ["C.S.", "COMPANIA", "EPISODIO", "APELLIDO PATERNO", "APELLIDO MATERNO",
                   "NOMBRE(S)", "HABITACION"]


def _celda(valor) -> str:
    """Texto limpio de una celda (los números enteros sin '.0')."""
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _filas_excel(ruta: Path) -> List[tuple]:
    """Filas de la primera hoja. Detecta .xlsx o .xls antiguo por el contenido, no por la extensión."""
    with open(ruta, "rb") as archivo:
        firma = archivo.read(8)
    if firma.startswith(b"PK"):
        libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
        try:
            return [tuple(fila) for fila in libro.active.iter_rows(values_only=True)]
        finally:
            libro.close()
    if firma.startswith(b"\xd0\xcf\x11\xe0"):
        try:
            import xlrd
        except ImportError as exc:
            raise ErrorLecturaDocumento(
                f"'{Path(ruta).name}' es un .xls antiguo. Instala el lector con: "
                "py -m pip install --user xlrd") from exc
        hoja = xlrd.open_workbook(str(ruta)).sheet_by_index(0)
        return [tuple(hoja.row_values(i)) for i in range(hoja.nrows)]
    raise ErrorFormatoDocumento("no es un archivo de Excel")


# ----------------------------------------------------------------------
# Detalle de un estado de cuenta (se lee solo cuando hace falta decidir)
# ----------------------------------------------------------------------
_RE_IMPRESO = re.compile(r"Fecha:\s*(\d{2})\.(\d{2})\.(\d{4})\s*Hora:\s*(\d{2}):(\d{2}):(\d{2})")
_RE_MONTO = re.compile(r"\d[\d,]*\.\d{2}-?")
_RE_CONDICION_SEGURO = re.compile(r"DEDUC\w*ASEG|COAS\w*ASEG")
_RE_COMPLEMENTARIO = re.compile(r"DEDUC\w*PART|COAS\w*PART")


def _monto(texto: str) -> float:
    """'4,022.81-' -> -4022.81"""
    valor = float(texto.rstrip("-").replace(",", ""))
    return -valor if texto.endswith("-") else valor


def _detalle_pdf(ruta: Path) -> DetalleCuenta:
    texto = extraer_texto_completo(ruta)
    mayus = texto.upper()
    detalle = DetalleCuenta(complementario=bool(_RE_COMPLEMENTARIO.search(mayus)))

    partes = re.split(r"Total a Pagar:", texto, flags=re.IGNORECASE)
    montos = [_monto(m) for m in _RE_MONTO.findall(partes[-1])] if len(partes) > 1 else []
    deducible = coaseguro = 0.0
    if len(montos) >= 9:                       # Importe ... Deducible, Coaseguro, Entrega, Total a Pagar
        deducible, coaseguro = montos[5], montos[6]
    if montos:
        detalle.total_a_pagar = montos[min(len(montos), 9) - 1]
    detalle.con_condiciones = bool(_RE_CONDICION_SEGURO.search(mayus)) or deducible != 0 or coaseguro != 0
    return detalle


def _detalle_excel(ruta: Path) -> DetalleCuenta:
    filas = _filas_excel(ruta)
    encabezados = [normalizar(str(c or "")) for c in filas[0]]
    detalle = DetalleCuenta()
    if "PRESTACION" in encabezados:
        i = encabezados.index("PRESTACION")
        detalle.con_condiciones = any(_RE_CONDICION_SEGURO.search(_celda(f[i]).upper())
                                      for f in filas[1:] if len(f) > i)
    if "TOTAL" in encabezados:
        i = encabezados.index("TOTAL")
        numeros = [f[i] for f in filas[1:] if len(f) > i and isinstance(f[i], (int, float))]
        if numeros:
            detalle.total_a_pagar = float(numeros[-1])
    return detalle


def leer_detalle(doc: Documento) -> DetalleCuenta:
    """Total, condiciones y carácter complementario de un documento. Nunca lanza excepción."""
    try:
        if doc.tipo == "pdf":
            return _detalle_pdf(doc.ruta)
        if doc.tipo == "xls":
            return _detalle_excel(doc.ruta)
    except Exception as exc:
        log.warning("No se pudo leer el detalle de '%s': %s", doc.nombre, exc)
    return DetalleCuenta()


# ----------------------------------------------------------------------
# Lector de estados de cuenta
# ----------------------------------------------------------------------
class LectorDocumentos:
    """Extrae episodio, paciente, compañía, categoría y habitación de PDF, TXT y Excel."""

    _HABITACION = re.compile(r"Habitaci[oó]n\s*:\s*([A-Z0-9]+)", re.IGNORECASE)
    _EPISODIO = re.compile(r"Episodio\s*:\s*(\d+)", re.IGNORECASE)
    _PACIENTE = re.compile(
        r"^\s*(.*?)(?=P.{1,2}gina\s*:|Fecha\s*:|Compa.{1,3}ia\s*:|M.{1,2}dico\s*:|$)",
        re.IGNORECASE | re.DOTALL)
    _COMPANIA = re.compile(
        r"Compa.{1,3}ia\s*:\s*(.*?)(?=M.{1,2}dico\s*:|P.{1,2}gina\s*:|$)",
        re.IGNORECASE | re.DOTALL)

    def __init__(self, config=None):
        self.palabras_broker = list(getattr(config, "palabras_broker", ["PDS"]))
        self.codigos_especializados = [str(c) for c in
                                       getattr(config, "codigos_servicios_especializados", ["2000000033"])]
        self.nombre_especializados = getattr(config, "nombre_servicios_especializados",
                                             "SERVICIOS ESPECIALIZADOS")

    @staticmethod
    def es_candidato(nombre_archivo: str) -> bool:
        """¿Puede ser un estado de cuenta? PDF, TXT (que no sea 'restart') o Excel."""
        nombre = nombre_archivo.lower()
        if nombre.startswith("~$"):                      # temporal de Excel
            return False
        if nombre.endswith((".xls", ".xlsx", ".xlsm")):
            return "bitacora" not in nombre
        return nombre.endswith(".pdf") or (nombre.endswith(".txt") and "restart" not in nombre)

    def _clasificar(self, compania: str) -> Categoria:
        texto = normalizar(compania)
        if "PARTICULAR" in texto:
            return Categoria.PARTICULAR
        for palabra in self.palabras_broker:
            if re.search(rf"\b{re.escape(palabra.upper())}\b", texto):
                return Categoria.BROKER
        return Categoria.SEGURO

    @staticmethod
    def _mtime(ruta: Path) -> float:
        try:
            return ruta.stat().st_mtime
        except OSError:
            return 0.0

    def leer(self, ruta: Path) -> Documento:
        ruta = Path(ruta)
        sufijo = ruta.suffix.lower()
        if sufijo == ".pdf":
            return self._leer_pdf(ruta)
        if sufijo == ".txt":
            return self._leer_txt(ruta)
        if sufijo in (".xls", ".xlsx", ".xlsm"):
            return self._leer_excel(ruta)
        raise ErrorLecturaDocumento(f"Tipo de archivo no soportado: {ruta.name}")

    # ---------------- PDF ----------------
    def _leer_pdf(self, ruta: Path) -> Documento:
        try:
            texto = leer_primera_pagina(ruta)
        except Exception as exc:
            raise ErrorLecturaDocumento(f"No se pudo leer el PDF '{ruta.name}': {exc}") from exc

        coincidencia_ep = self._EPISODIO.search(texto)
        if not coincidencia_ep:
            raise ErrorFormatoDocumento("no se encontró el campo 'Episodio'")
        episodio = coincidencia_ep.group(1)

        if "Paciente:" not in texto:
            raise ErrorFormatoDocumento("no se encontró el campo 'Paciente:'")
        despues = texto.split("Paciente:")[-1]
        coincidencia_pac = self._PACIENTE.search(despues)
        paciente = coincidencia_pac.group(1).replace("\n", " ").strip() if coincidencia_pac else ""
        if not paciente:
            raise ErrorFormatoDocumento("no se pudo leer el nombre del paciente")

        coincidencia_cia = self._COMPANIA.search(texto)
        compania = coincidencia_cia.group(1).replace("\n", " ").strip() if coincidencia_cia else ""
        if not compania:
            raise ErrorFormatoDocumento("no se encontró la compañía")

        coincidencia_hab = self._HABITACION.search(texto)
        habitacion = coincidencia_hab.group(1) if coincidencia_hab else "S/D"

        impreso = None
        m = _RE_IMPRESO.search(texto)
        if m:
            d, mes, a, h, mi, se = (int(x) for x in m.groups())
            try:
                impreso = datetime(a, mes, d, h, mi, se)
            except ValueError:
                impreso = None

        return Documento(ruta, "pdf", episodio, paciente, compania, self._clasificar(compania),
                         habitacion, impreso, self._mtime(ruta))

    # ---------------- TXT (SAP Hana) ----------------
    def _leer_txt(self, ruta: Path) -> Documento:
        try:
            with open(ruta, "r", encoding="latin-1") as archivo:
                lineas = archivo.readlines()
        except OSError as exc:
            raise ErrorLecturaDocumento(f"No se pudo leer el TXT '{ruta.name}': {exc}") from exc

        idx_ap_pat = idx_ap_mat = idx_nombres = -1
        idx_episodio = idx_compania = idx_credencial = -1
        episodio = paciente = compania = ""

        for linea in lineas:
            es_encabezado = (linea.startswith("|") and "Apellido Paterno" in linea
                             and "---" not in linea)
            if es_encabezado:
                encabezados = [col.strip() for col in linea.split("|")]
                try:
                    idx_ap_pat = encabezados.index("Apellido Paterno")
                    idx_ap_mat = encabezados.index("Apellido Materno")
                    idx_nombres = encabezados.index("Nombre(s)")
                except ValueError:
                    log.warning("Encabezado incompleto en '%s' (faltan columnas de nombre).", ruta.name)
                    idx_ap_pat = -1
                    continue
                for i, col in enumerate(encabezados):
                    mayus = col.upper()
                    if "EPISODIO" in mayus and idx_episodio == -1:
                        idx_episodio = i
                    elif "COMPA" in mayus or "CIA." in mayus:
                        idx_compania = i
                    elif "CREDENCIAL" in mayus:
                        idx_credencial = i
                continue

            es_dato = (idx_ap_pat != -1 and linea.startswith("|")
                       and "---" not in linea and "Apellido" not in linea)
            if not es_dato:
                continue

            columnas = [col.strip() for col in linea.split("|")]
            if len(columnas) <= max(idx_ap_pat, idx_ap_mat, idx_nombres):
                continue

            nombre = f"{columnas[idx_ap_pat]} {columnas[idx_ap_mat]} {columnas[idx_nombres]}"
            paciente = " ".join(nombre.split())

            if 0 <= idx_episodio < len(columnas):
                episodio = columnas[idx_episodio]
            else:
                coincidencia = re.search(r"(\d{10})", ruta.name)
                if coincidencia:
                    episodio = coincidencia.group(1)

            if 0 <= idx_compania < len(columnas):
                compania = columnas[idx_compania]
            elif idx_credencial != -1:
                compania = "BANORTE"
            break

        if idx_ap_pat == -1:
            raise ErrorVarianteTxt(
                f"TXT '{ruta.name}' con estructura errónea (variante SAP incorrecta: "
                "no se encontró el encabezado 'Apellido Paterno').")
        if not paciente or not episodio:
            raise ErrorFormatoDocumento("faltan datos de paciente o episodio en el TXT")
        if not compania:
            raise ErrorFormatoDocumento("no se encontró la compañía en el TXT")

        return Documento(ruta, "txt", episodio, paciente, compania, self._clasificar(compania),
                         modificado=self._mtime(ruta))

    # ---------------- Excel (servicios especializados) ----------------
    def _leer_excel(self, ruta: Path) -> Documento:
        try:
            filas = _filas_excel(ruta)
        except (ErrorLecturaDocumento, ErrorFormatoDocumento):
            raise
        except Exception as exc:
            raise ErrorLecturaDocumento(f"No se pudo leer el Excel '{ruta.name}': {exc}") from exc

        if not filas:
            raise ErrorFormatoDocumento("el Excel está vacío")
        encabezados = [normalizar(str(c or "")) for c in filas[0]]
        faltan = [c for c in _COLUMNAS_EXCEL if c not in encabezados]
        if faltan:
            raise ErrorFormatoDocumento(f"no tiene las columnas esperadas de servicios especializados ({', '.join(faltan)})")
        idx = {c: encabezados.index(c) for c in _COLUMNAS_EXCEL}

        primera = next((f for f in filas[1:] if len(f) > idx["EPISODIO"] and _celda(f[idx["EPISODIO"]])), None)
        if primera is None:
            raise ErrorFormatoDocumento("el Excel no tiene filas con episodio")

        codigo = _celda(primera[idx["COMPANIA"]])
        if codigo not in self.codigos_especializados:
            raise ErrorFormatoDocumento(
                f"el código de compañía {codigo} no está en 'codigos_servicios_especializados'")

        paciente = " ".join(" ".join(_celda(primera[idx[c]]) for c in
                                     ("APELLIDO PATERNO", "APELLIDO MATERNO", "NOMBRE(S)")).split())
        return Documento(ruta, "xls", _celda(primera[idx["EPISODIO"]]), paciente,
                         self.nombre_especializados, Categoria.SEGURO,
                         _celda(primera[idx["HABITACION"]]) or "S/D",
                         modificado=self._mtime(ruta))


# ----------------------------------------------------------------------
# Búsqueda de documentos adicionales en la carpeta
# ----------------------------------------------------------------------
class BuscadorDocumentos:
    """Localiza visados, controles de costos y resúmenes en una o varias carpetas.

    - Compara ignorando acentos y espacios, y con cada motor de PDF disponible.
    - Guarda en caché el texto de cada PDF para no releerlo en cada búsqueda.
    """

    def __init__(self, carpetas: Iterable[Path]):
        self.carpetas = [Path(c) for c in carpetas]
        self._cache: Dict[Path, List[str]] = {}      # ruta -> textos compactos (uno por motor)

    def _pdfs(self) -> List[Path]:
        archivos: List[Path] = []
        for carpeta in self.carpetas:
            try:
                archivos += [p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
            except OSError as exc:
                log.warning("No se pudo listar '%s': %s", carpeta, exc)
        return sorted(archivos, key=lambda p: (p.name.lower(), str(p)))

    def _textos(self, ruta: Path) -> List[str]:
        if ruta not in self._cache:
            compactos = [compactar(t) for t in textos_por_motor(ruta)]
            if not any(compactos):
                log.warning("'%s' no contiene texto legible (¿PDF escaneado como imagen?). "
                            "Solo se podrá identificar por su nombre.", ruta.name)
            self._cache[ruta] = compactos
        return self._cache[ruta]

    def _coincide(self, ruta: Path, claves: List[str]) -> bool:
        compactas = [compactar(c) for c in claves if compactar(c)]
        return any(all(c in texto for c in compactas) for texto in self._textos(ruta))

    def contiene(self, ruta: Path, clave: str) -> bool:
        return self._coincide(ruta, [clave])

    @staticmethod
    def _es_estado_de_cuenta(nombre: str) -> bool:
        nombre = nombre.upper()
        return "ASEG" in nombre or "PART" in nombre

    def por_nombre_y_clave(self, paciente: str, clave: str) -> Optional[Path]:
        """PDF que contiene la clave y el nombre del paciente (para archivos sin nombre fijo)."""
        palabras = [p for p in paciente.split() if len(compactar(p)) >= 3] or paciente.split()
        for ruta in self._pdfs():
            if self._coincide(ruta, [clave] + palabras):
                log.info("Documento '%s' encontrado: %s", clave, ruta.name)
                return ruta
        log.info("No se encontró documento '%s' para el paciente.", clave)
        return None

    def por_episodio_y_clave(self, episodio: str, clave: str) -> Optional[Path]:
        """PDF (que no sea estado de cuenta) con el episodio y la clave dentro."""
        for ruta in self._pdfs():
            if self._es_estado_de_cuenta(ruta.name):
                continue
            if self._coincide(ruta, [episodio, clave]):
                log.info("Documento '%s' encontrado: %s", clave, ruta.name)
                return ruta
        log.info("No se encontró documento '%s' para el episodio %s.", clave, episodio)
        return None

    def por_episodio_en_nombre(self, episodio: str, clave_nombre: str = "") -> Optional[Path]:
        """PDF cuyo NOMBRE contiene el episodio (y, si se indica, la clave; p. ej. VISADO)."""
        for ruta in self._pdfs():
            nombre = ruta.name.upper()
            if episodio in nombre and clave_nombre.upper() in nombre and not self._es_estado_de_cuenta(nombre):
                return ruta
        return None

    def visado_por_episodio(self, episodio: str) -> Optional[Path]:
        """Visado: PDF cuyo nombre contiene el episodio (VISADO<episodio>)."""
        ruta = self.por_episodio_en_nombre(episodio, "VISADO") or self.por_episodio_en_nombre(episodio)
        if ruta:
            log.info("Visado encontrado: %s", ruta.name)
        else:
            log.info("No se encontró visado local para el episodio %s.", episodio)
        return ruta
