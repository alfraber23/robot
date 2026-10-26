"""Automatización web: navegador, portal principal de expedientes y portal ECH.

Todas las esperas son EXPLÍCITAS (WebDriverWait): avanzan en cuanto el elemento
está listo y solo fallan cuando se agota el tiempo máximo de la configuración.
"""
import logging
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException, TimeoutException,
                                        UnexpectedAlertPresentException, WebDriverException)
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from configuracion import Config
from excepciones import ErrorPortal

_MAYUS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MINUS = "abcdefghijklmnopqrstuvwxyz"


def _mensaje(exc: Exception) -> str:
    """Primera línea del mensaje de una excepción de Selenium (sin el stacktrace)."""
    texto = (getattr(exc, "msg", None) or str(exc) or "").strip()
    return texto.splitlines()[0] if texto else exc.__class__.__name__


# ----------------------------------------------------------------------
class Navegador:
    """Ciclo de vida de Chrome."""

    def __init__(self, config: Config):
        self.config = config
        self.driver: Optional[webdriver.Chrome] = None
        self._log = logging.getLogger("WEB")

    def iniciar(self) -> webdriver.Chrome:
        self._log.info("Abriendo Chrome...")
        try:
            if self.config.chromedriver_path:
                self._log.info("Usando chromedriver: %s", self.config.chromedriver_path)
                self.driver = webdriver.Chrome(service=Service(self.config.chromedriver_path))
            else:
                self.driver = webdriver.Chrome()
        except WebDriverException as exc:
            raise ErrorPortal(
                "No se pudo abrir Chrome. Verifica que esté instalado y que Selenium pueda "
                "obtener el driver (o define 'chromedriver_path' en config.json). "
                f"Detalle: {_mensaje(exc)}") from exc
        return self.driver

    def cerrar(self) -> None:
        if self.driver is None:
            return
        try:
            self.driver.quit()
            self._log.info("Chrome cerrado.")
        except WebDriverException as exc:
            self._log.warning("Al cerrar Chrome: %s", _mensaje(exc))
        finally:
            self.driver = None


# ----------------------------------------------------------------------
class PortalBase:
    """Utilidades comunes: esperas explícitas y manejo de la pestaña propia."""

    def __init__(self, driver, config: Config, etiqueta: str):
        self.driver = driver
        self.cfg = config
        self.log = logging.getLogger(etiqueta)
        self.handle: Optional[str] = None

    def activar(self) -> None:
        """Cambia el foco de Selenium a la pestaña de este portal."""
        if self.handle:
            self.driver.switch_to.window(self.handle)

    def esperar(self, condicion, descripcion: str, timeout: Optional[int] = None):
        segundos = timeout or self.cfg.timeout_web
        self.log.debug("Esperando (máx %ss): %s", segundos, descripcion)
        try:
            return WebDriverWait(self.driver, segundos).until(condicion)
        except TimeoutException as exc:
            raise ErrorPortal(f"Tiempo agotado ({segundos}s) esperando: {descripcion}") from exc

    def esperar_opcional(self, condicion, descripcion: str, timeout: Optional[int] = None):
        """Como esperar(), pero devuelve None en lugar de lanzar error."""
        try:
            return self.esperar(condicion, descripcion, timeout)
        except ErrorPortal:
            self.log.debug("No apareció (opcional): %s", descripcion)
            return None

    def clic(self, locador, descripcion: str, timeout: Optional[int] = None):
        elemento = self.esperar(EC.element_to_be_clickable(locador), descripcion, timeout)
        elemento.click()
        return elemento

    def esperar_carga(self, elemento_previo=None) -> None:
        """Espera a que termine una navegación: el elemento anterior desaparece
        (si la página recarga) y el documento queda en estado 'complete'."""
        if elemento_previo is not None:
            try:
                WebDriverWait(self.driver, 5).until(EC.staleness_of(elemento_previo))
            except TimeoutException:
                pass  # la página no recargó (actualización por AJAX): es normal
        self.esperar(lambda d: d.execute_script("return document.readyState") == "complete",
                     "carga completa de la página")
        # Mejor esfuerzo: si el portal usa jQuery, se da unos segundos a que terminen sus peticiones
        # AJAX. NO es obligatorio (en algunas páginas nunca llega a cero): si no pasa, se continúa.
        try:
            WebDriverWait(self.driver, 3).until(
                lambda d: d.execute_script("return (typeof jQuery === 'undefined') || jQuery.active === 0;"))
        except TimeoutException:
            self.log.debug("Aún hay peticiones AJAX pendientes; se continúa.")

    def pausa(self, factor: float = 1.0) -> None:
        """Pausa corta de estabilización (configurable con 'pausa_ui'). Se usa solo donde el
        portal no da ninguna señal observable de que terminó de preparar la fila de carga."""
        time.sleep(self.cfg.pausa_ui * factor)


# ----------------------------------------------------------------------
class PortalExpedientes(PortalBase):
    """Portal principal donde se digitalizan los documentos."""

    def __init__(self, driver, config: Config):
        super().__init__(driver, config, "WEB")
        self.handle = driver.current_window_handle

    # ---------------- sesión / navegación ----------------
    def abrir_busqueda(self) -> None:
        """Abre la pantalla de búsqueda de expedientes (inicia sesión si hace falta)."""
        self.log.info("Abriendo portal principal...")
        try:
            self.driver.get(self.cfg.url_login)
            self.esperar(
                lambda d: d.find_elements(By.NAME, "noExpediente") or d.find_elements(By.NAME, "nameUser"),
                "pantalla de login o de búsqueda de expedientes")

            if self.driver.find_elements(By.NAME, "noExpediente"):
                self.log.info("Sesión ya activa.")
                return

            self.log.info("Iniciando sesión...")
            self.driver.find_element(By.NAME, "nameUser").send_keys(self.cfg.usuario)
            self.driver.find_element(By.NAME, "password").send_keys(self.cfg.password)
            try:
                self.driver.find_element(By.ID, "login").click()
            except NoSuchElementException:
                self.driver.find_element(By.NAME, "login").click()

            self.esperar(EC.presence_of_element_located((By.NAME, "noExpediente")),
                         "pantalla de búsqueda tras iniciar sesión (¿credenciales incorrectas?)")
            self.log.info("Sesión iniciada correctamente.")
        except WebDriverException as exc:
            raise ErrorPortal(f"Fallo al abrir/iniciar sesión en el portal principal: {_mensaje(exc)}") from exc

    def buscar_y_abrir_episodio(self, episodio: str) -> None:
        self.log.info("Buscando episodio %s...", episodio)
        try:
            campo = self.esperar(EC.presence_of_element_located((By.NAME, "noExpediente")),
                                 "campo de búsqueda de expediente")
            campo.clear()
            campo.send_keys(episodio)
            self.clic((By.ID, "envioEpisodio"), "botón de búsqueda de episodio")

            boton_abrir = self.esperar(
                EC.element_to_be_clickable((By.XPATH, "//img[@title='Abrir']")),
                f"resultado del episodio {episodio} (¿existe en el portal?)")
            boton_abrir.click()
            try:
                self.esperar_carga(boton_abrir)
            except ErrorPortal as exc:
                self.log.warning("%s. Se continúa de todos modos.", exc)
            self.pausa()
            self.log.info("Episodio %s abierto.", episodio)
        except WebDriverException as exc:
            raise ErrorPortal(f"Fallo al abrir el episodio {episodio}: {_mensaje(exc)}") from exc

    def validar_aseguradora(self, compania_doc: str) -> bool:
        """Seguro anti-cruces: el portal debe mostrar la misma aseguradora que el documento."""
        compania = compania_doc.upper()
        palabra = next((p.upper() for p in self.cfg.palabras_validacion_portal if p.upper() in compania), None)
        if not palabra:
            self.log.info("Aseguradora atípica (%s). Filtro estricto omitido.", compania_doc)
            return True
        try:
            texto = self.driver.find_element(By.TAG_NAME, "body").text.upper()
        except WebDriverException as exc:
            self.log.warning("No se pudo validar la aseguradora (%s). Se continúa.", _mensaje(exc))
            return True
        if palabra in texto:
            self.log.info("El portal coincide con el documento (%s).", palabra)
            return True
        self.log.error("CRÍTICO: cruce detectado. Documento: %s | El portal no muestra esa aseguradora. "
                       "Se aborta la inyección para proteger el expediente.", palabra)
        return False

    # ---------------- subida de archivos ----------------
    def cargar_archivo(self, ruta: Path, tipo_documento: str) -> bool:
        """Sube un archivo a la fila `tipo_documento`.

        True  = subido, o ya estaba digitalizado, o la fila no admite carga.
        False = la fila no existe o hubo un error (queda registrado en consola).
        """
        self.log.info("Intentando subir: '%s' (%s)", tipo_documento, Path(ruta).name)
        base = (f"//tr[contains(translate(., '{_MAYUS}', '{_MINUS}'), '{tipo_documento.lower()}')]")
        icono_ok = f"{base}//img[contains(@src, 'y.png')]"
        try:
            if not self.driver.find_elements(By.XPATH, base):
                self.log.warning("La fila '%s' no existe en este expediente. Omitiendo...", tipo_documento)
                return False

            if self.driver.find_elements(By.XPATH, icono_ok):
                self.log.info("'%s' ya fue digitalizado previamente.", tipo_documento)
                return True

            if not self.driver.find_elements(By.XPATH, f"{base}//input[@type='file']"):
                self.log.info("'%s' no tiene campo de carga disponible. Omitiendo...", tipo_documento)
                return True

            enviado = False
            for intento in range(1, self.cfg.intentos_subida + 1):
                if intento > 1:
                    self.log.warning("Reintentando '%s' (intento %d/%d, pausas x%d)...",
                                     tipo_documento, intento, self.cfg.intentos_subida, intento)
                if self._intento_de_subida(base, ruta, tipo_documento, factor=intento):
                    enviado = True
                    break
            if not enviado:
                raise ErrorPortal(
                    f"El portal respondió 'No hay documentos por enviar' en {self.cfg.intentos_subida} "
                    f"intento(s) al subir '{tipo_documento}'.")

            confirmado = self.esperar_opcional(
                lambda d: d.find_elements(By.XPATH, icono_ok),
                f"confirmación visual de '{tipo_documento}'",
                self.cfg.timeout_confirmacion_subida)
            if confirmado:
                self.log.info("Archivo subido con éxito y confirmado en el portal.")
            else:
                self.log.warning("Archivo enviado, pero el portal no mostró la confirmación de '%s'. "
                                 "Revísalo manualmente.", tipo_documento)
            return True
        except (ErrorPortal, WebDriverException) as exc:
            detalle = exc if isinstance(exc, ErrorPortal) else _mensaje(exc)
            self.log.error("Error al subir '%s': %s", tipo_documento, detalle)
            return False

    def _intento_de_subida(self, base: str, ruta: Path, tipo_documento: str, factor: int) -> bool:
        """Un intento completo de subida. False si el portal dijo 'No hay documentos por enviar'."""
        campo = self.driver.find_elements(By.XPATH, f"{base}//input[@type='file']")[0]
        campo.send_keys(str(Path(ruta).resolve()))
        adjunto = campo.get_attribute("value") or ""
        if adjunto:
            self.log.debug("Archivo adjuntado en el navegador: %s", adjunto)
        else:
            self.log.warning("El navegador no registró el archivo en el campo de carga.")
        self.pausa(factor)

        self.clic((By.XPATH, f"{base}//input[@id='enviar' or @value='Enviar']"),
                  f"botón 'Enviar' de '{tipo_documento}'")
        self._descartar_alerta()
        self.pausa(factor)

        boton_envio = self.esperar(EC.element_to_be_clickable((By.ID, "envio")), "botón final de envío")
        try:
            boton_envio.click()
            try:
                self.esperar_carga(boton_envio)
            except ErrorPortal as exc:
                self.log.warning("%s. Se verificará la confirmación de todos modos.", exc)
        except UnexpectedAlertPresentException as exc:
            texto = getattr(exc, "alert_text", None) or str(exc)
            self._cerrar_alerta()
            if "NO HAY DOCUMENTOS" in texto.upper():
                self.log.warning("El portal respondió: 'No hay documentos por enviar'.")
                return False
            raise ErrorPortal(f"Alerta inesperada del portal al enviar: {texto}") from exc
        return True

    def _cerrar_alerta(self) -> None:
        try:
            self.driver.switch_to.alert.accept()
        except WebDriverException:
            pass   # Selenium ya la había cerrado

    def _descartar_alerta(self) -> None:
        try:
            alerta = WebDriverWait(self.driver, self.cfg.timeout_alerta).until(EC.alert_is_present())
            texto = alerta.text
            alerta.dismiss()
            self.log.info("Alerta del portal descartada: %s", texto)
        except TimeoutException:
            self.log.debug("El portal no mostró alerta.")


# ----------------------------------------------------------------------
class PortalECH(PortalBase):
    """Portal ECH: de aquí se lee la hora de alta del paciente."""

    def __init__(self, driver, config: Config):
        super().__init__(driver, config, "ECH")
        self.conectado = False

    def iniciar_sesion(self, historial: bool = False) -> None:
        """historial=True: usa la pestaña 'Altas clínicas (últimos 10 días)' en lugar de la de hoy."""
        self.log.info("Iniciando sesión en portal ECH (pestaña nueva)...")
        t = self.cfg.timeout_ech
        try:
            self.driver.execute_script("window.open('');")
            self.handle = self.driver.window_handles[-1]
            self.activar()
            self.driver.get(self.cfg.url_ech)

            usuario = self.esperar(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']")),
                                   "campo de usuario de ECH", t)
            clave = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            usuario.send_keys(self.cfg.usuario_ech)
            clave.send_keys(self.cfg.password_ech)

            self.clic((By.XPATH, "//button[.//span[text()='Acceder']]"), "botón 'Acceder' de ECH", t)
            self.clic((By.XPATH, "//img[contains(@src, 'Gestion-de-hospitalizados')]"),
                      "módulo 'Gestión de hospitalizados' (¿credenciales ECH incorrectas?)", t)
            self.clic((By.ID, "home-navigate-to-discharge-38"), "tarjeta de altas", t)
            minusculas = f"translate(text(), '{_MAYUS}', '{_MINUS}')"
            if historial:
                self.log.info("Fecha anterior: se usará la pestaña 'Altas clínicas (últimos 10 días)'.")
                self.clic((By.XPATH, f"//span[contains({minusculas}, 'altas cl') and contains({minusculas}, 'ltimos')]"),
                          "pestaña 'Altas clínicas (últimos 10 días)'", t)
            else:
                self.clic((By.XPATH, f"//span[contains({minusculas}, 'altas cl') and not(contains({minusculas}, 'ltimos'))]"),
                          "pestaña 'Altas clínicas'", t)

            if self.esperar_opcional(EC.presence_of_element_located((By.XPATH, "//tbody//tr")),
                                     "filas en la tabla de altas", t):
                self.log.info("Conectado y en espera en la tabla de altas.")
            else:
                self.log.warning("Conectado, pero la tabla de altas aún no muestra filas.")
            self.conectado = True
        except WebDriverException as exc:
            self.conectado = False
            raise ErrorPortal(f"Fallo en la conexión con ECH: {_mensaje(exc)}") from exc
        except ErrorPortal:
            self.conectado = False
            raise

    def extraer_hora_alta(self, episodio: str) -> str:
        """Hora de alta del episodio, 'NO_ENCONTRADA' o 'ERROR'. Nunca lanza excepción."""
        self.log.info("Consultando hora de alta para Ep: %s...", episodio)
        xpath_fila = f"//tr[.//span[contains(text(), '{episodio}')]]"
        try:
            fila = self.esperar_opcional(
                EC.presence_of_element_located((By.XPATH, xpath_fila)),
                f"fila del episodio {episodio} en la tabla de altas", self.cfg.timeout_ech_fila)
            if fila is None:
                self.log.warning("Paciente no encontrado en la tabla de altas.")
                return "NO_ENCONTRADA"

            celda = self.driver.find_element(By.XPATH, f"{xpath_fila}/td[2]")
            texto = celda.text.strip()
            self.log.info("Texto de la celda de hora en la tabla: %r", texto)
            hora = texto.split(" ")[-1] if " " in texto else texto
            self.log.info("Hora detectada: %s", hora)
            return hora
        except WebDriverException as exc:
            self.log.error("Fallo al leer la hora de alta: %s", _mensaje(exc))
            return "ERROR"
