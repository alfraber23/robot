"""Reglas de negocio: catálogos de aseguradoras y normalización de nombres."""
from typing import Iterable

# Aseguradoras cuyo estado de cuenta debe venir en TXT (se omite el PDF)
PDF_REQUIERE_TXT = ("GNP", "PROVINCIAL", "MONTERREY", "BANORTE")

# Aseguradoras que llevan visado local
SEGUROS_CON_VISADO = ("AXA", "GNP", "PROVINCIAL", "ALLIANZ", "VE POR MAS", "ZURICH")

# Aseguradoras que llevan "Resumen de importes"
SEGUROS_CON_RESUMEN = ("BANORTE", "PLAN SEGURO")

# Palabras que se buscan en el portal para validar que no hay cruce de expedientes
PALABRAS_VALIDACION_PORTAL = (
    "METLIFE", "BANORTE", "PLAN SEGURO", "AXA", "GNP",
    "PROVINCIAL", "ALLIANZ", "ZURICH", "MONTERREY",
    "MAPFRE", "BBVA", "MSH",
)

# (palabras clave, nombre estándar). El ORDEN importa: gana la primera coincidencia.
_ASEGURADORAS = (
    (("MONTERREY", "NEW YORK"), "MONTERREY"),
    (("METLIFE",), "METLIFE"),
    (("BANORTE",), "BANORTE"),
    (("AXA",), "AXA"),
    (("GNP", "NACIONAL PROVINCIAL"), "GNP"),
    (("PLAN SEGURO",), "PLAN SEGURO"),
    (("ALLIANZ",), "ALLIANZ"),
    (("ZURICH",), "ZURICH"),
    (("MAPFRE",), "MAPFRE"),
    (("INBURSA",), "INBURSA"),
    (("BUPA",), "BUPA"),
    (("ATLAS",), "ATLAS"),
    (("VE POR MAS", "BX+"), "VE POR MAS"),
    (("BBVA",), "BBVA"),
    (("MDABROAD",), "MDABROAD"),
    (("SHRINERS",), "SHRINERS"),
)

_AREAS_FIJAS = {
    "CRSUCE": "UCE",
    "CRSALCC": "CORO",
    "CRSALTIP": "UTI",
    "CRSSHEMO": "HEMO",
    "CRSSENDO": "ENDO",
}


def contiene_alguna(texto: str, claves: Iterable[str]) -> bool:
    """True si alguna de las claves aparece en el texto (sin importar mayúsculas)."""
    texto = texto.upper()
    return any(clave in texto for clave in claves)


def formato_requerido(compania: str, nombre_especializados: str) -> str:
    """Formato del estado de cuenta que se sube para esa compañía: 'pdf', 'txt' o 'xls'."""
    if compania.strip().upper() == nombre_especializados.strip().upper():
        return "xls"
    if contiene_alguna(compania, PDF_REQUIERE_TXT):
        return "txt"
    return "pdf"


def normalizar_aseguradora(compania_cruda: str) -> str:
    """Estandariza el nombre de la aseguradora para la bitácora."""
    compania = compania_cruda.upper()
    for claves, nombre in _ASEGURADORAS:
        if any(clave in compania for clave in claves):
            return nombre
    return compania_cruda.strip()


def normalizar_habitacion(hab_cruda: str) -> str:
    """Traduce los códigos de área de SAP Hana a nombres legibles."""
    hab = str(hab_cruda).strip().upper()
    if hab in _AREAS_FIJAS:
        return _AREAS_FIJAS[hab]
    if hab.startswith("CRHACI"):
        resto = hab[len("CRHACI"):]
        try:
            return f"CIM-{int(resto)}"
        except ValueError:
            return f"CIM-{resto}"
    if hab.startswith("CRHA"):
        return hab[len("CRHA"):]
    return hab
