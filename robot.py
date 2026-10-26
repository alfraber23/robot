"""Orquestador: coordina carpetas, selección de documentos, portales, auditoría y bitácora."""
import logging
import time
from contextlib import contextmanager, nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional

from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException

from auditoria import AuditoriaFinanciera
from bitacora import Bitacora
from carpetas import ResolutorCarpetas
from configuracion import DIR_BASE, Config
from documentos import (BuscadorDocumentos, LectorDocumentos, extraer_texto_completo,
                        limpiar_temporales, ultima_pagina_temporal)
from excepciones import ErrorCarpeta, ErrorDigitalizador, ErrorPortal
from interaccion import Interaccion
from inventario import construir_inventario
from modelos import DatosManuales, RegistroBitacora, Resultado, Tarea, filas_a_tsv
from portal import Navegador, PortalECH, PortalExpedientes
from reglas import (SEGUROS_CON_RESUMEN, SEGUROS_CON_VISADO, contiene_alguna,
                    normalizar_aseguradora)
from seleccion import resolver_expediente
from servidor import ServidorLocal

from datetime import date, datetime, timedelta   # si timedelta no estaba
from correo import CorreoAlta, ProveedorCorreoOutlook

log = logging.getLogger("SYS")
log_manual = logging.getLogger("MANUAL")
log_tiempo = logging.getLogger("TIEMPO")


class RobotDigitalizador:
    def __init__(self, config: Config, interaccion: Interaccion, operador: str, fecha: date):
        self.cfg = config
        self.ui = interaccion
        self.operador = operador
        self.fecha = fecha

        self.resolutor = ResolutorCarpetas(config)
        self.lector = LectorDocumentos(config)
        self.buscador: BuscadorDocumentos = None      # se crea al conocer las carpetas
        self.carpetas: List[Path] = []
        self.auditoria = AuditoriaFinanciera(
            lambda mensaje: self.ui.confirmar("Auditoría financiera", mensaje),
            config.tolerancia_auditoria)
        self.bitacora = Bitacora(config.ruta_bitacora)
        self.servidor = ServidorLocal(config)
        self.navegador = Navegador(config)
        self.portal: PortalExpedientes = None
        self.ech: PortalECH = None
        self._ech_omitido = False

        self._correo: Optional[ProveedorCorreoOutlook] = None
        self.correos_altas: List[CorreoAlta] = []

        self.resultados = {r: 0 for r in Resultado}
        self.incidencias: List[str] = []              # errores por documento
        self.faltantes: List[str] = []                # documentos por subir / conseguir a mano
        self.no_reconocidos: List[Path] = []          # archivos que nadie reclama
        self.registros_nuevos: List[RegistroBitacora] = []
        self._ruta_respaldo = DIR_BASE / "logs" / f"pegar_bitacora_{datetime.now():%Y%m%d_%H%M%S}.txt"
        self._inicio = time.perf_counter()

    # ==================================================================
    # Punto de entrada (se ejecuta en un hilo aparte)
    # ==================================================================
    def ejecutar(self) -> None:
        exito = False
        try:
            log.info("Robot iniciado. Operador: %s | Fecha de las altas: %s",
                     self.operador, self.fecha.strftime("%d.%m.%Y"))
            self._flujo()
            self._reportar_final()
            exito = True
        except ErrorDigitalizador as exc:
            log.error("PROCESO DETENIDO: %s", exc)
            self.ui.estado(f"Error: {exc}", "error")
        except Exception:
            log.exception("PROCESO DETENIDO por un error inesperado")
            self.ui.estado("Error inesperado. Revisa la consola / logs.", "error")
        finally:
            if self._correo is not None:
                try:
                    self._correo.desconectar()
                except Exception:
                    pass
                self._correo = None
            self.navegador.cerrar()
            limpiar_temporales()
            self.servidor.detener()
            self.ui.terminado(exito)

    @contextmanager
    def _medir(self, nombre: str):
        inicio = time.perf_counter()
        try:
            yield
        finally:
            log_tiempo.info("%s: %.1f s", nombre, time.perf_counter() - inicio)

    # ==================================================================
    # Flujo general
    # ==================================================================
    def _flujo(self) -> None:
        with self._medir("Buscar carpetas y listar archivos"):
            candidatos = self._buscar_candidatos()
        with self._medir("Precargar correos de altas"):
            self._precargar_correos()
        with self._medir("Leer y clasificar archivos"):
            tareas = self._inventariar(candidatos)
        with self._medir("Filtrar por correo"):
            tareas = self._filtrar_por_correo(tareas)
        self._preparar_bitacora()
        try:
            with self._medir("Servidor local"):
                self._iniciar_servidor()
            with self._medir("Chrome y portales"):
                self._iniciar_navegador()

            total = len(tareas)
            for numero, tarea in enumerate(tareas, start=1):
                self.ui.estado(f"Procesando {numero}/{total}: {tarea.doc.paciente}")
                inicio = time.perf_counter()
                resultado = self._procesar(tarea)
                self.resultados[resultado] += 1
                log_tiempo.info("Paciente %d/%d (%s): %.1f s", numero, total,
                                tarea.doc.episodio, time.perf_counter() - inicio)
        finally:
            with self._medir("Guardado final de bitácora"):
                self._guardar_bitacora_final()

    # ---------------- carpetas e inventario ----------------
    def _buscar_candidatos(self) -> List[Path]:
        self.ui.estado("Buscando la carpeta del día...")
        if self.cfg.usar_carpeta_por_fecha:
            self.carpetas = self.resolutor.carpetas_para(self.fecha)
        else:
            self.carpetas = [Path(self.cfg.carpeta_documentos)]
            log.info("Modo carpeta fija: %s", self.carpetas[0])
        self.buscador = BuscadorDocumentos(self.carpetas)

        candidatos: List[Path] = []
        for carpeta in self.carpetas:
            try:
                archivos = sorted(carpeta.iterdir())
            except OSError as exc:
                raise ErrorCarpeta(f"No se pudo leer la carpeta '{carpeta}': {exc}") from exc
            encontrados = [p for p in archivos if p.is_file() and LectorDocumentos.es_candidato(p.name)]
            log.info("%s: %d archivo(s) PDF/TXT/Excel.", carpeta, len(encontrados))
            candidatos += encontrados

        if not candidatos:
            raise ErrorDigitalizador("No hay documentos (PDF/TXT/Excel) en las carpetas de trabajo.")
        return candidatos

    def _preguntar_opcion(self, titulo: str, mensaje: str, opciones: List[str], sugerida: int) -> Optional[int]:
        return self.ui.elegir_opcion(titulo, mensaje, opciones, sugerida)

    def _inventariar(self, candidatos: List[Path]) -> List[Tarea]:
        self.ui.estado("Clasificando archivos...")
        inventario = construir_inventario(candidatos, self.lector, self.buscador)
        self.incidencias.extend(inventario.incidencias)
        self.no_reconocidos = inventario.no_reconocidos
        if inventario.no_reconocidos:
            log.warning("Archivos que NO se pudieron clasificar (revísalos a mano):")
            for ruta in inventario.no_reconocidos:
                log.warning("  - %s  (%s)", ruta.name, ruta.parent.name)

        self.ui.estado("Eligiendo el estado de cuenta de cada episodio...")
        tareas: List[Tarea] = []
        for exp in inventario.expedientes:
            res = resolver_expediente(exp, self.cfg.nombre_servicios_especializados,
                                      self.cfg.palabras_broker, self._preguntar_opcion)
            for ignorado in res.ignorados:
                log.info("Ep %s | no se sube: %s", exp.episodio, ignorado)
            if res.faltante:
                log.warning("FALTANTE: %s", res.faltante)
                self.faltantes.append(res.faltante)
            if res.tarea:
                tareas.append(res.tarea)

        if not tareas:
            for texto in self.faltantes:
                log.warning("  - %s", texto)
            raise ErrorDigitalizador("No hay estados de cuenta que se puedan procesar en las carpetas.")
        log.info("Episodios por procesar: %d", len(tareas))
        return tareas

    def _filtrar_por_correo(self, tareas: List[Tarea]) -> List[Tarea]:
        """Deja solo las tareas que tienen un correo de alta correspondiente.

        Si Outlook no está disponible (o el filtro está deshabilitado), se
        devuelven todas las tareas (comportamiento anterior).
        """
        if not self.cfg.filtrar_por_correo:
            log.info("Filtro por correo deshabilitado. Se procesan las %d tarea(s).",
                     len(tareas))
            return tareas

        if not self.correos_altas:
            log.warning("Sin correos de altas precargados: no se puede filtrar. "
                        "Se procesan todas las %d tarea(s) por seguridad.", len(tareas))
            return tareas

        filtradas: List[Tarea] = []
        omitidas: List[Tarea] = []
        for tarea in tareas:
            match = self._correlacionar(tarea.doc)
            if match:
                log.info("Ep %s (%s): alta confirmada en correo (%s, %s).",
                         tarea.doc.episodio, tarea.doc.paciente,
                         match.operador or match.operador_firma or "?",
                         match.hora_cajas)
                filtradas.append(tarea)
            else:
                log.info("Ep %s (%s): SIN alta en correo. Se omite (aún no se ha trabajado).",
                         tarea.doc.episodio, tarea.doc.paciente)
                omitidas.append(tarea)

        if omitidas:
            log.warning("%d episodio(s) omitido(s) por no tener alta en correo:", len(omitidas))
            for t in omitidas:
                log.warning("  - Ep %s | %s | %s",
                            t.doc.episodio, t.doc.paciente, t.doc.compania)

        log.info("Tareas filtradas: %d de %d (omitidas: %d).",
                 len(filtradas), len(tareas), len(omitidas))

        if not filtradas:
            raise ErrorDigitalizador(
                "Ningún episodio de la carpeta tiene alta registrada en correo. "
                "Revisa que los analistas hayan enviado los correos de alta o "
                "deshabilita 'filtrar_por_correo' en config.json.")

        return filtradas

        self.ui.estado("Eligiendo el estado de cuenta de cada episodio...")
        tareas: List[Tarea] = []
        for exp in inventario.expedientes:
            res = resolver_expediente(exp, self.cfg.nombre_servicios_especializados,
                                      self.cfg.palabras_broker, self._preguntar_opcion)
            for ignorado in res.ignorados:
                log.info("Ep %s | no se sube: %s", exp.episodio, ignorado)
            if res.faltante:
                log.warning("FALTANTE: %s", res.faltante)
                self.faltantes.append(res.faltante)
            if res.tarea:
                tareas.append(res.tarea)

        if not tareas:
            for texto in self.faltantes:
                log.warning("  - %s", texto)
            raise ErrorDigitalizador("No hay estados de cuenta que se puedan procesar en las carpetas.")
        log.info("Episodios por procesar: %d", len(tareas))
        return tareas

    # ---------------- preparación ----------------
    def _preparar_bitacora(self) -> None:
        if not self.bitacora.existe():
            log.warning("No se encontró '%s'. Los datos NO se guardarán en bitácora local.", self.bitacora.ruta)
            self.ui.estado("Aviso: no se encontró el Excel local. Solo se generará el texto para pegar.", "aviso")
            return
        self.ui.estado("Comprobando permisos del Excel...", "aviso")
        self.bitacora.abrir()      # lanza ErrorBitacora si está abierto / dañado

    def _iniciar_servidor(self) -> None:
        self.ui.estado("Iniciando servidor local del portal...")
        self.servidor.iniciar()

    def _iniciar_navegador(self) -> None:
        self.ui.estado("Iniciando conexión web (Chrome)...")
        driver = self.navegador.iniciar()
        self.portal = PortalExpedientes(driver, self.cfg)
        self.portal.abrir_busqueda()

        self.ech = PortalECH(driver, self.cfg)
        dias = (date.today() - self.fecha).days
        if dias < 0 or dias > self.cfg.dias_historial_ech:
            self._ech_omitido = True
            log.info("La fecha (%s) está fuera del rango del ECH (hoy y hasta %d días atrás): "
                     "no se consultará la hora de alta.", self.fecha.strftime("%d.%m.%Y"),
                     self.cfg.dias_historial_ech)
            self.portal.activar()
            return
        historial = dias > 0
        if historial:
            log.info("Las altas son de hace %d día(s): se consultará el ECH en 'últimos %d días'.",
                     dias, self.cfg.dias_historial_ech)
        self.ui.estado("Conectando con el portal ECH...")
        try:
            self.ech.iniciar_sesion(historial)
        except ErrorPortal as exc:
            log.error("ECH no disponible: %s. Se continuará SIN hora de alta.", exc)
            self.ui.estado("Aviso: ECH no disponible, se continúa sin hora de alta.", "aviso")
            self._incidencia(f"ECH no disponible: {exc}")
        self.portal.activar()

    # ---------------- correo ----------------
    def _precargar_correos(self) -> None:
        """Conecta a Outlook y precarga las altas (una sola vez por corrida)."""
        if not self.cfg.habilitar_correo:
            log.info("Correo deshabilitado en config.json. Se omite precarga.")
            return
        try:
            self._correo = ProveedorCorreoOutlook(self.cfg)
            self._correo.conectar()
            desde = self.fecha - timedelta(days=1)
            hasta = self.fecha + timedelta(days=1)
            self.correos_altas = self._correo.buscar_altas(desde, hasta)
            con_firma = sum(1 for c in self.correos_altas if c.operador)
            log.info("Correo: %d altas precargadas (%s → %s). Con firma legible: %d.",
                     len(self.correos_altas),
                     desde.strftime("%d.%m.%Y"), hasta.strftime("%d.%m.%Y"),
                     con_firma)
        except ErrorCorreo as exc:
            log.warning("Correo no disponible: %s. Se continúa sin datos del correo.", exc)
            self.correos_altas = []
        except Exception as exc:
            log.exception("Error inesperado precargando correos: %s", exc)
            self.correos_altas = []

    def _correlacionar(self, doc) -> Optional[CorreoAlta]:
        """Busca la alta que corresponde a este documento.

        Estrategia:
          1. Filtrar por fecha (± 1 día alrededor del tiempo del documento).
          2. Preferir match por episodio (el más fuerte).
          3. Si no, match por nombre del paciente (mín. 2 palabras).
        """
        if not self.correos_altas:
            return None

        fecha_doc = doc.tiempo.date()
        candidatos = [
            c for c in self.correos_altas
            if abs((c.enviado.date() - fecha_doc).days) <= 1
        ]
        if not candidatos:
            return None

        # 1) Match por episodio
        ep = str(doc.episodio).strip()
        if ep and len(ep) >= 6:
            con_ep = [c for c in candidatos if ep in c.asunto]
            if con_ep:
                return max(con_ep, key=lambda c: c.enviado)

        # 2) Fallback: nombre del paciente
        from documentos import normalizar as _norm
        palabras = [p for p in _norm(doc.paciente).split() if len(p) >= 4]
        if not palabras:
            return None
        mejor, mejor_score = None, 0
        for c in candidatos:
            asunto_norm = _norm(c.asunto)
            score = sum(1 for p in palabras if p in asunto_norm)
            if score > mejor_score:
                mejor_score = score
                mejor = c
        minimo = max(2, len(palabras) - 1)
        if mejor and mejor_score >= minimo:
            return mejor
        return None

    # ==================================================================
    # Procesamiento de un documento (con red de seguridad)
    # ==================================================================
    def _procesar(self, tarea: Tarea) -> Resultado:
        doc = tarea.doc
        try:
            return self._procesar_documento(tarea)
        except (NoSuchWindowException, InvalidSessionIdException) as exc:
            raise ErrorPortal("Chrome se cerró o perdió la sesión durante el proceso.") from exc
        except ErrorDigitalizador as exc:
            log.error("[%s] %s", doc.nombre, exc)
            self._incidencia(f"{doc.nombre} (Ep {doc.episodio}): {exc}")
        except Exception:
            log.exception("[%s] Error inesperado procesando el documento", doc.nombre)
            self._incidencia(f"{doc.nombre} (Ep {doc.episodio}): error inesperado (ver traceback arriba)")
        self._recuperar_portal()
        return Resultado.ERROR

    def _recuperar_portal(self) -> None:
        """Tras un error, deja el portal listo para el siguiente documento."""
        try:
            self.portal.activar()
            self.portal.abrir_busqueda()
        except Exception as exc:
            log.warning("No se pudo reiniciar el portal tras el error: %s", exc)

    def _procesar_documento(self, tarea: Tarea) -> Resultado:
        doc = tarea.doc
        log.info("=" * 50)
        log.info("Procesando %s: %s (%s)", doc.tipo.upper(), doc.nombre, doc.ruta.parent.name)
        log.info("Datos -> Ep: %s | Paciente: %s | Cía: %s | Hab: %s",
                 doc.episodio, doc.paciente, doc.compania, doc.habitacion)

        compania = doc.compania
        if doc.es_particular:
            modo = "hemo" if doc.habitacion == "HEMO" else "particular"
            log.info("Paciente 100%% Particular (%s). Pasando directo a bitácora...", modo)
            compania = "PARTICULAR"
        elif doc.es_shriners:
            modo = "simple"
            log.info("Seguro SHRINERS. Pasando directo a bitácora...")
        else:
            modo = "completo"
            if not self._flujo_web(doc):
                return Resultado.OMITIDO

        self._fase_bitacora(doc, compania, modo, tarea.broker)
        return Resultado.PROCESADO

    # ------------------------------------------------------------------
    def _flujo_web(self, doc) -> bool:
        """Sube los documentos del episodio. False si se aborta por cruce de aseguradora."""
        self.portal.buscar_y_abrir_episodio(doc.episodio)
        if not self.portal.validar_aseguradora(doc.compania):
            autorizado = self.ui.confirmar(
                "Posible cruce de aseguradora",
                f"El portal no muestra la aseguradora del documento ({doc.compania}).\n"
                f"Paciente: {doc.paciente}\nEpisodio: {doc.episodio}\n\n"
                "¿Subir los documentos de todos modos?")
            if not autorizado:
                self._incidencia(f"{doc.nombre} (Ep {doc.episodio}): CRUCE de aseguradora, no se subió nada.")
                self.portal.abrir_busqueda()
                return False
            log.warning("El operador autorizó continuar pese al aviso de cruce (Ep %s).", doc.episodio)

        compania = doc.compania.upper()

        # Estado de cuenta (AXA en PDF: solo la última hoja)
        if "AXA" in compania and doc.tipo == "pdf":
            contexto = ultima_pagina_temporal(doc.ruta, doc.episodio)
        else:
            contexto = nullcontext(doc.ruta)
        with contexto as ruta_estado_cuenta:
            self._subir(doc, ruta_estado_cuenta, "estado de cuenta")

        # MetLife: control de costos (nombre libre: se busca dentro del archivo)
        if "METLIFE" in compania:
            ruta = self._buscar_o_pedir(
                doc, "el control de costos de MetLife",
                lambda: self.buscador.por_nombre_y_clave(doc.paciente, "Control de Costos"))
            if ruta:
                self._subir(doc, ruta, "reporte de control de costos")

        # Visados locales con auditoría
        if contiene_alguna(compania, SEGUROS_CON_VISADO):
            ruta_visado = self._buscar_o_pedir(
                doc, "el visado", lambda: self.buscador.visado_por_episodio(doc.episodio))
            if ruta_visado:
                log.info("Iniciando auditoría local para %s...", doc.compania)
                texto_edo = extraer_texto_completo(doc.ruta)
                texto_vis = extraer_texto_completo(ruta_visado)
                if self.auditoria.validar_cuadre(texto_edo, texto_vis):
                    self._subir(doc, ruta_visado, "visado")
                else:
                    log.warning("Visado bloqueado. No se subió al portal.")
                    self._faltante(doc, "el visado (no cuadra / rechazado)")

        # Banorte / Plan Seguro: resumen de importes (CtrlGastos<episodio> por nombre; si no, por contenido)
        if contiene_alguna(compania, SEGUROS_CON_RESUMEN):
            ruta = self._buscar_o_pedir(
                doc, "el resumen de importes (control de gastos)",
                lambda: (self.buscador.por_episodio_en_nombre(doc.episodio, "CTRLGASTOS")
                         or self.buscador.por_episodio_y_clave(doc.episodio, "RESUMEN DE IMPORTES")))
            if ruta:
                log.info("Resumen de importes: %s", ruta.name)
                self._subir(doc, ruta, "Control de gastos")
        return True

    def _buscar_o_pedir(self, doc, descripcion: str, buscar: Callable[[], Optional[Path]]) -> Optional[Path]:
        """Busca un documento adicional; si no aparece, deja que el operador lo seleccione a mano."""
        ruta = buscar()
        if ruta:
            return ruta
        log.warning("No se encontró %s de %s (Ep %s). Se pide al operador.", descripcion, doc.paciente, doc.episodio)
        self.ui.estado(f"Falta {descripcion}: {doc.paciente}", "aviso")
        elegido = self.ui.pedir_archivo(
            f"Falta {descripcion}",
            f"No se encontró {descripcion} de:\n{doc.paciente}\nEpisodio {doc.episodio} - {doc.compania}",
            self.carpetas[0])
        if elegido:
            log.info("Archivo seleccionado a mano para %s: %s", descripcion, elegido)
            return Path(elegido)
        self._faltante(doc, descripcion)
        return None

    def _subir(self, doc, ruta: Path, tipo: str) -> bool:
        ok = self.portal.cargar_archivo(ruta, tipo)
        if not ok:
            self._incidencia(f"{doc.nombre} (Ep {doc.episodio}): no se pudo subir '{tipo}'.")
        return ok

    def _faltante(self, doc, que: str) -> None:
        mensaje = f"{doc.paciente} (Ep {doc.episodio}, {doc.compania}): NO se encontró {que}. Agrégalo manualmente."
        log.warning("FALTANTE: %s", mensaje)
        self.faltantes.append(mensaje)

    # ------------------------------------------------------------------
    def _fase_bitacora(self, doc, compania: str, modo: str, broker_sugerido: str) -> None:
        ya_registrado = (self.bitacora.contiene(doc.episodio)
                         or any(r.episodio == doc.episodio for r in self.registros_nuevos))
        if ya_registrado:
            log.info("El episodio %s ya está en la bitácora local. Se omite captura manual y registro.",
                     doc.episodio)
        else:
            # Buscar el alta en el correo (ya filtrada al inicio, pero volvemos a obtener
            # el match para tener hora y operador).
            match = self._correlacionar(doc) if self.correos_altas else None
            if match:
                log_manual.info(
                    "Correo: Ep %s | hora=%s | operador=%r (firma: %r)",
                    doc.episodio, match.hora_cajas,
                    match.operador or "?", match.operador_firma or "?")

            # Construir "sugeridos" desde el correo. Solo precargamos lo que el correo
            # puede darnos hoy: la hora a cajas. Los demás campos siguen siendo manuales.
            sugeridos = DatosManuales()
            if match and match.hora_cajas:
                sugeridos.hora_cajas = match.hora_cajas

            self.ui.estado(f"Esperando datos manuales para: {doc.paciente}", "aviso")
            log_manual.info("Solicitando datos en la interfaz para: %s (modo %s)",
                            doc.paciente, modo)
            datos = self.ui.pedir_datos_manuales(
                doc.paciente, doc.episodio, modo, broker_sugerido, sugeridos=sugeridos)
            log_manual.info("Datos capturados: %s", datos)

            hora_alta = self._obtener_hora_alta(doc.episodio)

            # Operador de la bitácora: el de la firma del correo (quien trabajó el alta).
            # Si el correo no tiene firma, cae al operador que arrancó el robot.
            operador_bitacora = self.operador
            if match and match.operador:
                operador_bitacora = match.operador

            registro = RegistroBitacora(
                episodio=doc.episodio, habitacion=doc.habitacion, paciente=doc.paciente,
                seguro=normalizar_aseguradora(compania), operador=operador_bitacora,
                datos=datos, hora_alta=hora_alta)

            if self.bitacora.disponible:
                if self.bitacora.registrar(registro):
                    self._guardar_para_pegar(registro)
            else:
                self._guardar_para_pegar(registro)

        if modo == "completo":
            self.portal.abrir_busqueda()

    def _guardar_para_pegar(self, registro: RegistroBitacora) -> None:
        """Anota la fila en el archivo de texto (instantáneo) para no perderla si algo falla."""
        self.registros_nuevos.append(registro)
        try:
            self._ruta_respaldo.parent.mkdir(exist_ok=True)
            with open(self._ruta_respaldo, "a", encoding="utf-8") as archivo:
                archivo.write(filas_a_tsv([registro]) + "\n")
        except OSError as exc:
            log.warning("No se pudo anotar la fila en el respaldo de texto: %s", exc)

    def _obtener_hora_alta(self, episodio: str) -> str:
        if self._ech_omitido:
            return "NO_ENCONTRADA"
        if not self.ech.conectado:
            log.warning("ECH no está conectado: hora de alta = ERROR.")
            return "ERROR"
        try:
            self.ech.activar()
            return self.ech.extraer_hora_alta(episodio)
        finally:
            self.portal.activar()

    # ==================================================================
    # Cierre y reportes
    # ==================================================================
    def _guardar_bitacora_final(self) -> None:
        if not self.bitacora.disponible:
            return
        self.ui.estado("Escribiendo datos finales en el disco duro...")
        log.info("Guardado final de la bitácora...")
        self.bitacora.guardar_con_respaldo()

    def _incidencia(self, texto: str) -> None:
        self.incidencias.append(texto)

    def _reportar_final(self) -> None:
        log.info("=" * 50)
        log.info("PROCESO MASIVO COMPLETADO en %.1f min", (time.perf_counter() - self._inicio) / 60)
        log.info("Procesados: %d | Omitidos: %d | Con error: %d",
                 self.resultados[Resultado.PROCESADO], self.resultados[Resultado.OMITIDO],
                 self.resultados[Resultado.ERROR])

        if self.faltantes:
            log.warning("FALTANTES para resolver manualmente (%d):", len(self.faltantes))
            for texto in self.faltantes:
                log.warning("  - %s", texto)
        if self.incidencias:
            log.warning("Incidencias a revisar (%d):", len(self.incidencias))
            for texto in self.incidencias:
                log.warning("  - %s", texto)
        if self.no_reconocidos:
            log.warning("Archivos sin clasificar (%d): %s", len(self.no_reconocidos),
                        ", ".join(p.name for p in self.no_reconocidos))

        self._entregar_resultado()

        pendientes = len(self.faltantes) + len(self.incidencias) + len(self.no_reconocidos)
        if pendientes:
            self.ui.estado(f"Completado con {pendientes} aviso(s) por revisar. Mira la consola.", "aviso")
        else:
            self.ui.estado("¡Proceso Masivo Completado!", "ok")
        log.info("=" * 50)

    def _entregar_resultado(self) -> None:
        """Filas nuevas para pegar en el Excel web (columnas B a L, sin 'No.')."""
        if not self.registros_nuevos:
            log.info("No hubo filas nuevas para pegar en la bitácora.")
            return
        tsv = filas_a_tsv(self.registros_nuevos)
        log.info("FILAS PARA PEGAR EN LA BITÁCORA (%d), desde la columna B (Episodio):", len(self.registros_nuevos))
        for linea in tsv.splitlines():
            log.info("  %s", linea.replace("\t", " | "))
        log.info("Copia del texto para pegar: %s", self._ruta_respaldo)
        self.ui.mostrar_resultado(tsv, len(self.registros_nuevos))
