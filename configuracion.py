"""Configuración central.

Los valores por defecto (incluidas credenciales) viven aquí. Para cambiarlos
sin tocar el código, crea un archivo `config.json` junto a main.py con SOLO
las claves que quieras sobreescribir, por ejemplo:

    {"password": "NUEVA_CLAVE", "usuario_ech": "otro.usuario"}
"""
import json
import logging
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional

from excepciones import ErrorConfiguracion
from reglas import PALABRAS_VALIDACION_PORTAL

from dataclasses import dataclass, field, fields
from typing import List, Optional

log = logging.getLogger("CFG")

if getattr(sys, "frozen", False):          # ejecutable creado con PyInstaller
    DIR_BASE = Path(sys.executable).resolve().parent
else:
    DIR_BASE = Path(__file__).resolve().parent
ARCHIVO_CONFIG = DIR_BASE / "config.json"


@dataclass
class Config:
    # --- Portal principal (inyección) ---
    url_login: str = "http://127.0.0.1:8000/scan/index"
    usuario: str = "JUAREZMMS"
    password: str = "JUAREZMMS2018"

    # --- Portal ECH ---
    url_ech: str = "https://ech.grupoempresarialangeles.com.mx/ech-pm/#/login"
    usuario_ech: str = "josealberto.frausto"
    password_ech: str = "Beto022099!"

    # --- Servidor local del portal (equivale al .cmd que se ejecutaba a mano) ---
    servidor_habilitado: bool = True
    servidor_python: str = "python"      # el que abre Python 2.7 en esta PC
    servidor_manage: str = r"C:\SetupScan\scan\manage.py"
    servidor_argumentos: List[str] = field(default_factory=lambda: ["--noreload"])
    servidor_reiniciar: bool = True      # cierra un servidor previo antes de arrancar
    servidor_detener_al_terminar: bool = True

    # --- Reglas de documentos ---
    # Aseguradoras cuyo nombre debe aparecer en el portal (seguro anti-cruces). Las que no
    # estén aquí se consideran "atípicas" y no se validan. Se pueden agregar desde config.json.
    palabras_validacion_portal: List[str] = field(default_factory=lambda: list(PALABRAS_VALIDACION_PORTAL))
    # Si la compañía de un estado de cuenta contiene alguna de estas palabras, es un
    # documento de broker (CxC): se identifica pero NO se sube ni se captura.
    palabras_broker: List[str] = field(default_factory=lambda: ["PDS"])
    # Servicios especializados: estado de cuenta en Excel. El código de compañía
    # (columna "Compania" del Excel) funciona como filtro.
    codigos_servicios_especializados: List[str] = field(default_factory=lambda: ["2000000033"])
    nombre_servicios_especializados: str = "SERVICIOS ESPECIALIZADOS"

    # --- Carpetas de trabajo ---
    # Estructura: <carpeta_altas>\ALTAS <año>\<n>. <MES> <año>\<dd.mm.aaaa>
    carpeta_altas: str = r"\\192.160.106.23\Public\Analisis de cuentas\Ancts\ALTAS"
    usar_carpeta_por_fecha: bool = True
    # Carpeta "digitaliza": si es None se busca una llamada así dentro de la carpeta
    # del día y junto a ALTAS. Si no la encuentra, define aquí su ruta completa.
    carpeta_digitaliza: Optional[str] = None
    # Solo se usa cuando usar_carpeta_por_fecha es false (modo local / pruebas)
    carpeta_documentos: str = str(DIR_BASE)

    # --- Bitácora local (respaldo) ---
    archivo_bitacora: str = "Bitacora_Prueba.xlsx"
    carpeta_bitacora: str = str(DIR_BASE)

    # --- Chrome ---
    # Si Selenium no logra descargar el driver solo (red restringida),
    # apunta aquí a un chromedriver.exe ya descargado.
    chromedriver_path: Optional[str] = None

    # --- Tiempos máximos de espera (segundos). Son TOPES, no pausas fijas ---
    timeout_servidor: int = 30
    timeout_web: int = 15
    # Pausas de estabilización del portal (segundos). El portal no da señal de cuándo termina
    # de preparar la fila de carga; si falla "No hay documentos por enviar" se reintenta
    # con pausas mayores (pausa_ui x número de intento).
    pausa_ui: float = 1.0
    intentos_subida: int = 3
    # El ECH muestra las altas de hoy y, en otra pestaña, las de los últimos N días.
    # Con una fecha más antigua que esto no se consulta la hora de alta.
    dias_historial_ech: int = 10
    timeout_ech: int = 20
    timeout_ech_fila: int = 5
    timeout_alerta: int = 2
    timeout_confirmacion_subida: int = 10

    # --- Auditoría financiera ---
    tolerancia_auditoria: float = 2.00

    #CODIGO DE DEEPSEEK PARA INTENTAR IMPLEMENTAR EL CORREO *******************************************************************
    buzon_correo: str = "supervisorcuentashac@saludangeles.mx"
    # Si True, solo se procesan episodios que tengan un correo de alta.
    # Si Outlook no está disponible, se cae al comportamiento anterior (procesar todos).
    filtrar_por_correo: bool = True
    # --- Correo (Outlook de escritorio) ---
    habilitar_correo: bool = True           # por seguridad, empieza apagado

    operadores_firma: List[str] = field(default_factory=lambda: [
        "Alberto", "Angela", "Mariana", "Karina",
        "Erick", "Antonio", "Paty", "Angy", "Denisse"
    ])

    correos_analistas: List[str] = field(default_factory=lambda: [
        "operadorcuentashac1@saludangeles.mx",
        "operadorcuentashac2@saludangeles.mx",
        "supervisorcuentashac@saludangeles.mx",
        "angeles.juarez@saludangeles.mx",
    ])
    correos_cajas: List[str] = field(default_factory=lambda: [
        "admcaja1@saludangeles.mx", "admcaja1.hdc@saludangeles.mx",
        "admcaja2@saludangeles.mx", "admcaja2.hdc@saludangeles.mx",
        "admcaja3@saludangeles.mx", "admcaja3.hdc@saludangeles.mx",
        "admcaja4@saludangeles.mx", "admcaja4.hdc@saludangeles.mx",
        "admcaja5@saludangeles.mx", "admcaja5.hdc@saludangeles.mx",
        "marcela.ramirez@saludangeles.mx",
        "admision.cscr@saludangeles.mx",
        "cajas.cscr@saludangeles.mx",
    ])
    # Cada entrada: [buzón, tipo_de_carpeta]. buzón "" = el del usuario actual.
    # tipo: "sent" | "inbox" | nombre literal (lo que muestre el diagnóstico).
    carpetas_correo: List[List[str]] = field(default_factory=lambda: [
        ["supervisorcuentashac@saludangeles.mx", "sent"],
        ["supervisorcuentashac@saludangeles.mx", "inbox"],
    ])
    dias_busqueda_correo: int = 3
    #*****************************************************

    @property
    def ruta_bitacora(self) -> Path:
        return Path(self.carpeta_bitacora) / self.archivo_bitacora

    @classmethod
    def cargar(cls, ruta: Path = ARCHIVO_CONFIG) -> "Config":
        config = cls()
        if not ruta.exists():
            log.info("Sin config.json: se usan los valores por defecto.")
            return config

        try:
            with open(ruta, "r", encoding="utf-8") as archivo:
                datos = json.load(archivo)
        except (OSError, ValueError) as exc:
            raise ErrorConfiguracion(f"No se pudo leer {ruta.name}: {exc}") from exc

        if not isinstance(datos, dict):
            raise ErrorConfiguracion(f"{ruta.name} debe contener un objeto JSON {{...}}")

        validas = {campo.name for campo in fields(cls)}
        for clave, valor in datos.items():
            if clave in validas:
                setattr(config, clave, valor)
                log.info("Valor '%s' tomado de %s", clave, ruta.name)
            else:
                log.warning("Clave desconocida en %s ignorada: '%s'", ruta.name, clave)
        return config


