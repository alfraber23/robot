"""Automatización web: navegador, portal principal de expedientes y portal ECH.

Todas las esperas son EXPLÍCITAS (WebDriverWait): avanzan en cuanto el elemento
está listo y solo fallan cuando se agota el tiempo máximo de la configuración.
"""
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

    _XPATH_SIGUIENTE_PAGINA = (
        "//button[contains(@class, 'p-paginator-next')]"
        " | //*[local-name()='svg' and contains(@class, 'p-paginator-icon')]"
        "[.//*[local-name()='path' and starts-with(@d, 'M5.25 11.1728')]]"
    )
    _RE_EPISODIO = re.compile(r"\b(\d{7,12})\b")

    def __init__(self, driver, config: Config):
        super().__init__(driver, config, "ECH")
        self.conectado = False
        self._cache_horas: Dict[str, str] = {}
        self._tabla_completa_leida = False

    def _clic_ech(self, locador, descripcion: str, timeout: Optional[int] = None):
        """Clic robusto para componentes PrimeNG/SVG (usa fallback JS si el clic nativo es interceptado)."""
        elem = self.esperar(EC.presence_of_element_located(locador), descripcion, timeout)
        try:
            WebDriverWait(self.driver, min(timeout or self.cfg.timeout_ech, 5)).until(
                EC.element_to_be_clickable(locador)
            )
            elem.click()
        except WebDriverException:
            self.driver.execute_script(
                "const root = arguments[0];"
                "const inner = root.querySelector ? root.querySelector('.p-multiselect-trigger, .p-multiselect, .p-checkbox-box') : null;"
                "const el = inner || root.closest('button, .p-multiselect, .p-checkbox-box, [role=\"checkbox\"]') || root;"
                "if (typeof el.click === 'function') { el.click(); } "
                "else { el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); }",
                elem,
            )
        return elem

    def _configurar_filtro_engrane(self, timeout: int) -> None:
        """Abre el engrane de 'altas clínicas (últimos 10 días)', despliega #admissionTypeItem,
        marca todos, activa 'Incluir dados de alta = Sí' y presiona Buscar.
        """
        self.log.info("Configurando filtro en el engrane de 'Altas clínicas (últimos 10 días)'...")
        # 1) Clic en el engrane correspondiente a "altas clínicas (últimos 10 días)" (5.º engrane):
        minusculas = f"translate(text(), '{_MAYUS}', '{_MINUS}')"
        xpath_engrane = (
            f"(//span[contains({minusculas}, 'altas cl') and contains({minusculas}, 'ltimos')]"
            f"/following::i[contains(@class, 'pi-cog')])[1]"
            f" | (//i[contains(@class, 'pi-cog')])[5]"
        )
        self._clic_ech(
            (By.XPATH, xpath_engrane),
            "engrane de 'altas clínicas (últimos 10 días)' (i.pi.pi-cog)",
            timeout,
        )
        self.pausa(0.5)

        # 2) Clic en la lista desplegable por ID: <p-multiselect id="admissionTypeItem">
        self.esperar(
            EC.presence_of_element_located((By.ID, "admissionTypeItem")),
            "contenedor #admissionTypeItem",
            timeout,
        )
        self._clic_ech(
            (
                By.CSS_SELECTOR,
                "#admissionTypeItem .p-multiselect-trigger, #admissionTypeItem .p-multiselect, #admissionTypeItem",
            ),
            "lista desplegable (#admissionTypeItem)",
            timeout,
        )
        self.pausa(0.5)

        # 3) Clic en marcar todos: <div role="checkbox" class="p-checkbox-box" aria-checked="false">
        xpath_check = (
            "//div[contains(@class, 'p-multiselect-header')]//div[@role='checkbox' and contains(@class, 'p-checkbox-box')]"
            " | (//div[@role='checkbox' and contains(@class, 'p-checkbox-box') and @aria-checked='false'])[1]"
        )
        checkbox = self.esperar(
            EC.presence_of_element_located((By.XPATH, xpath_check)),
            "casilla 'Marcar todos' (p-checkbox-box)",
            timeout,
        )
        if (checkbox.get_attribute("aria-checked") or "").lower() != "true":
            self._clic_ech((By.XPATH, xpath_check), "casilla 'Marcar todos'", timeout)
            self.pausa(0.5)
        else:
            self.log.info("La casilla 'Marcar todos' ya estaba marcada.")

        # 4) "Incluir en la búsqueda los dados de alta" -> clic en botón "Sí"
        xpath_si_dados_alta = (
            "(//span[contains(normalize-space(), 'Incluir en la b') or contains(normalize-space(), 'dados de alta')]"
            "/following::div[@role='button' and (@aria-label='Sí' or @aria-labelledby='Sí' "
            "or .//span[contains(@class, 'p-button-label') and (normalize-space()='Sí' or normalize-space()='Si')])])[1]"
            " | //div[@role='button' and (@aria-label='Sí' or @aria-labelledby='Sí')]"
        )
        boton_si = self.esperar(
            EC.presence_of_element_located((By.XPATH, xpath_si_dados_alta)),
            "botón 'Sí' de 'Incluir en la búsqueda los dados de alta'",
            timeout,
        )
        if (boton_si.get_attribute("aria-pressed") or "").lower() != "true":
            self._clic_ech(
                (By.XPATH, xpath_si_dados_alta),
                "botón 'Sí' (Incluir en la búsqueda los dados de alta)",
                timeout,
            )
            self.pausa(0.5)
        else:
            self.log.info("El botón 'Sí' (Incluir dados de alta) ya estaba seleccionado.")

        # 5) Clic en Buscar: <button id="search-worklist-save-worklist"><span class="p-button-label">Buscar</span></button>
        self._clic_ech(
            (
                By.XPATH,
                "//button[@id='search-worklist-save-worklist']"
                " | //button[.//span[contains(@class, 'p-button-label') and normalize-space()='Buscar']]",
            ),
            "botón 'Buscar' (search-worklist-save-worklist)",
            timeout,
        )
        self.pausa(1.0)

    def iniciar_sesion(self, historial: bool = False) -> None:
        """Ingresa a ECH, abre siempre 'Altas clínicas (últimos 10 días)' y aplica el filtro del engrane."""
        self.log.info("Iniciando sesión en portal ECH (pestaña nueva)...")
        t = self.cfg.timeout_ech
        self._cache_horas.clear()
        self._tabla_completa_leida = False
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
            self.log.info("Abriendo pestaña 'Altas clínicas (últimos 10 días)'...")
            self.clic(
                (By.XPATH, f"//span[contains({minusculas}, 'altas cl') and contains({minusculas}, 'ltimos')]"),
                "pestaña 'Altas clínicas (últimos 10 días)'",
                t,
            )

            self._configurar_filtro_engrane(t)

            if self.esperar_opcional(EC.presence_of_element_located((By.XPATH, "//tbody//tr")),
                                     "filas en la tabla de altas", t):
                self.log.info("Conectado y tabla de altas cargada tras aplicar filtro.")
            else:
                self.log.warning("Conectado, pero la tabla de altas aún no muestra filas.")
            self.conectado = True
        except WebDriverException as exc:
            self.conectado = False
            raise ErrorPortal(f"Fallo en la conexión con ECH: {_mensaje(exc)}") from exc
        except ErrorPortal:
            self.conectado = False
            raise

    def _leer_pagina_actual(self, episodio_buscado: str) -> Tuple[Optional[str], Tuple[str, ...]]:
        """Lee la página actual de la tabla, guarda en caché (episodio -> hora) y devuelve
        (hora_del_episodio_buscado_si_aparece, huella_de_filas_de_la_pagina).
        """
        huella: List[str] = []
        hora_encontrada: Optional[str] = None

        filas = self.driver.find_elements(By.XPATH, "//tbody//tr")
        for fila in filas:
            try:
                texto_fila = (fila.text or "").strip()
                if not texto_fila:
                    continue
                huella.append(texto_fila)
                celdas = fila.find_elements(By.TAG_NAME, "td")
                if len(celdas) < 2:
                    continue
                texto_hora = celdas[1].text.strip()
                hora = texto_hora.split(" ")[-1] if " " in texto_hora else texto_hora
                if not hora:
                    continue
                for ep_match in self._RE_EPISODIO.findall(texto_fila):
                    self._cache_horas.setdefault(ep_match, hora)
                    if ep_match == episodio_buscado:
                        hora_encontrada = hora
            except WebDriverException:
                continue

        # Verificación directa por XPath del episodio por si el formato difiere del regex
        if hora_encontrada is None:
            xpath_fila = f"//tr[.//span[contains(text(), '{episodio_buscado}')]]"
            if self.driver.find_elements(By.XPATH, xpath_fila):
                celda = self.driver.find_element(By.XPATH, f"{xpath_fila}/td[2]")
                texto = celda.text.strip()
                hora_encontrada = texto.split(" ")[-1] if " " in texto else texto
                self._cache_horas[episodio_buscado] = hora_encontrada

        return hora_encontrada, tuple(huella)

    def _avanzar_pagina(self) -> bool:
        """Da clic en el botón de página siguiente. Devuelve False si ya no hay más páginas."""
        elementos = self.driver.find_elements(By.XPATH, self._XPATH_SIGUIENTE_PAGINA)
        if not elementos:
            return False

        # Preferir el elemento visible; verificar si su botón contenedor está deshabilitado
        objetivo = next((e for e in elementos if e.is_displayed()), elementos[0])
        esta_deshabilitado = self.driver.execute_script(
            "const btn = arguments[0].closest('button') || arguments[0];"
            "return Boolean(btn.disabled || btn.classList.contains('p-disabled') "
            "|| btn.getAttribute('aria-disabled') === 'true');",
            objetivo,
        )
        if esta_deshabilitado:
            return False

        self.driver.execute_script(
            "const btn = arguments[0].closest('button') || arguments[0];"
            "if (typeof btn.click === 'function') { btn.click(); } "
            "else { btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); }",
            objetivo,
        )
        self.pausa(0.8)
        return True

    def extraer_hora_alta(self, episodio: str) -> str:
        """Hora de alta del episodio recorriendo la paginación, 'NO_ENCONTRADA' o 'ERROR'."""
        episodio = str(episodio).strip()
        self.log.info("Consultando hora de alta para Ep: %s...", episodio)

        if episodio in self._cache_horas:
            hora = self._cache_horas[episodio]
            self.log.info("Hora detectada (en caché de ECH): %s", hora)
            return hora

        if self._tabla_completa_leida:
            self.log.warning("El episodio %s no está en ninguna página de la tabla de altas.", episodio)
            return "NO_ENCONTRADA"

        try:
            self.esperar_opcional(
                EC.presence_of_element_located((By.XPATH, "//tbody//tr")),
                "filas en la tabla de altas",
                self.cfg.timeout_ech_fila,
            )

            huella_anterior: Tuple[str, ...] = ()
            max_paginas = 50
            for pagina in range(1, max_paginas + 1):
                hora, huella_actual = self._leer_pagina_actual(episodio)
                if hora:
                    self.log.info("Hora detectada en página %d: %s", pagina, hora)
                    return hora

                if not huella_actual:
                    self.log.warning("La tabla de altas no muestra filas.")
                    return "NO_ENCONTRADA"

                if huella_anterior and huella_actual == huella_anterior:
                    self.log.info("La tabla se repitió en la página %d; fin de paginación alcanzado.", pagina)
                    self._tabla_completa_leida = True
                    break

                huella_anterior = huella_actual
                if not self._avanzar_pagina():
                    self.log.info("No hay más páginas en la tabla de altas (fin en página %d).", pagina)
                    self._tabla_completa_leida = True
                    break
                self.log.debug("Avanzando a la siguiente página de ECH (página %d)...", pagina + 1)

            self.log.warning("Paciente (Ep %s) no encontrado tras recorrer las páginas de ECH.", episodio)
            return "NO_ENCONTRADA"
        except WebDriverException as exc:
            self.log.error("Fallo al leer la hora de alta: %s", _mensaje(exc))
            return "ERROR"
