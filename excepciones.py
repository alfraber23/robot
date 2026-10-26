"""Excepciones propias del digitalizador.

Todas heredan de ErrorDigitalizador, así el robot puede distinguir los
errores "esperados" (con mensaje claro) de los bugs inesperados.
"""


class ErrorDigitalizador(Exception):
    """Base de todos los errores controlados del robot."""


class ErrorConfiguracion(ErrorDigitalizador):
    """Problema con config.json u otra configuración."""


class ErrorLecturaDocumento(ErrorDigitalizador):
    """No se pudo abrir o leer un documento."""


class ErrorFormatoDocumento(ErrorLecturaDocumento):
    """El documento se abrió, pero no tiene el formato de un estado de cuenta."""


class ErrorVarianteTxt(ErrorFormatoDocumento):
    """TXT de SAP con estructura incorrecta (variante equivocada)."""


class ErrorBitacora(ErrorDigitalizador):
    """Problema al abrir o guardar la bitácora de Excel."""


class ErrorPortal(ErrorDigitalizador):
    """Problema al interactuar con un portal web (login, esperas, subida)."""


class ErrorServidor(ErrorDigitalizador):
    """No se pudo arrancar o alcanzar el servidor local del portal."""


class ErrorCarpeta(ErrorDigitalizador):
    """No se encontró (o no se pudo leer) la carpeta de trabajo."""


#CODIGO DE DEEPSEEK PARA INTENTAR IMPLEMENTAR EL CORREO
class ErrorCorreo(ErrorDigitalizador):
    """Problema al leer correos desde Outlook."""
#*****************************************************