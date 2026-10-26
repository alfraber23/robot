"""Lectura de correos desde Outlook de escritorio (win32com).

Requiere Windows + Outlook instalado con la cuenta configurada.
No requiere Azure ni permisos de admin: usa la sesión de Outlook.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import time
import unicodedata

from excepciones import ErrorCorreo

log = logging.getLogger("MAIL")

try:
    import pythoncom
    import win32com.client
    _DISPONIBLE = True
except ImportError:
    _DISPONIBLE = False


_OL_FOLDERS = {
    "inbox": 6, "sent": 5, "drafts": 16, "outbox": 4,
    "deleted": 3, "calendar": 9, "contacts": 10,
}
_NOMBRES_CARPETA = {
    "inbox": ["Bandeja de entrada", "Inbox"],
    "sent": ["Elementos enviados", "Sent Items", "Sent"],
    "drafts": ["Borradores", "Drafts"],
    "deleted": ["Elementos eliminados", "Deleted Items"],
}

# Propiedades MAPI para extraer la dirección SMTP real (no el DN de Exchange)
_PR_SMTP_RECIP = "http://schemas.microsoft.com/mapi/proptag/0x39FE001E"
_PR_SMTP_SENDER = "http://schemas.microsoft.com/mapi/proptag/0x0C1F001E"

# ----------------------------------------------------------------------
# Detección del operador desde la firma del cuerpo
# ----------------------------------------------------------------------
_OPERADORES = [
    ("Alberto", ["ALBERTO"]),
    ("Angela",  ["ANGELA"]),
    ("Mariana", ["MARIANA"]),
    ("Karina",  ["KARINA"]),
    ("Erick",   ["ERICK"]),
    ("Antonio", ["ANTONIO"]),
    ("Paty",    ["PATY", "PATRICIA"]),
    ("Angy",    ["ANGY", "ANGELES JUAREZ"]),
    ("Denisse", ["DENISSE", "CEBALLOS"])
]

_CARGOS_FIRMA = [
    "Supervisor de Análisis de Cuentas",
    "Supervisora de Análisis de Cuentas",
    "Supervisor Análisis de Cuentas",
    "Analista de Cuentas",
    "Análisis de Cuentas",
]

_TITULOS_FIRMA = ("LIC.", "ING.", "DR.", "DRA.", "MTRO.", "MTRA.", "SR.", "SRA.")


def _normalizar_firma(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.upper().split())


def _limpiar_nombre_firma(nombre: str) -> str:
    n = nombre.strip()
    for titulo in _TITULOS_FIRMA:
        if n.upper().startswith(titulo + " "):
            return n[len(titulo):].strip()
    return n


def _extraer_firma(cuerpo: str) -> tuple:
    """Devuelve (nombre_firma, cargo) o ("", "") si no encuentra bloque de firma."""
    lineas = [l.strip() for l in (cuerpo or "").splitlines() if l.strip()]

    # Primera pasada: cargos específicos primero (Supervisor > Analista > Análisis)
    for cargo in _CARGOS_FIRMA:
        for i, linea in enumerate(lineas):
            if linea == cargo and i > 0:
                candidato = _limpiar_nombre_firma(lineas[i - 1])
                # Descartar líneas que claramente no son nombres
                if candidato and not candidato.upper().startswith(("TEL", "<", "HTTP")):
                    return candidato, cargo
    return "", ""


def _identificar_operador(nombre_firma: str) -> str:
    """Devuelve el nombre canónico ('Karina', 'Paty', ...) o "" si no matchea."""
    if not nombre_firma:
        return ""
    n = _normalizar_firma(nombre_firma)
    for canonico, alias in _OPERADORES:
        for a in alias:
            if a in n:
                return canonico
    return ""

@dataclass
class CorreoAlta:
    """Un correo de alta ya extraído de Outlook."""
    asunto: str
    remitente_nombre: str
    remitente_email: str
    enviado: datetime
    destinatarios: List[str]
    cuerpo_texto: str = ""
    adjuntos: List[str] = field(default_factory=list)
    carpeta_origen: str = ""
    # Campos derivados de la firma del cuerpo:
    operador_firma: str = ""      # nombre literal de la firma ("Karina Cervantes")
    cargo_firma: str = ""         # cargo de la firma ("Analista de Cuentas")
    operador: str = ""            # nombre canónico ("Karina", "Paty", ...) o ""

    @property
    def hora_cajas(self) -> str:
        return self.enviado.strftime("%H:%M")


@dataclass
class CorreoBroker:
    """Un correo de broker (o reenviado por Julio) para llenar el campo Broker/PDS."""
    broker: str          # "VIMED", "HS", "AMES", "AXIS", "ARSAM", "PROMEDIC"
    asunto: str
    remitente: str
    enviado: datetime
    cuerpo_texto: str = ""
    adjuntos: List[str] = field(default_factory=list)


class ProveedorCorreoOutlook:
    """Lee correos desde Outlook de escritorio."""

    def __init__(self, config: Config):
        self.cfg = config
        self._app = None
        self._ns = None
        self._analistas = {a.lower() for a in config.correos_analistas}
        self._cajas = {c.lower() for c in config.correos_cajas}
        self._cache_smtp: Dict[str, str] = {}   # DN o addr -> SMTP (cache para no repetir lookups)
        self._stats: Dict[str, int] = {
            "sin_asunto_alta": 0,
            "sin_remitente": 0,
            "no_analista": 0,
            "sin_caja": 0,
            "aceptados": 0,
            "procesados": 0,
        }

    # ---------------- conexión ----------------
    def conectar(self) -> None:
        if not _DISPONIBLE:
            raise ErrorCorreo(
                "pywin32 no está instalado. Instálalo con:\n"
                "  py -m pip install --user pywin32\n"
                "Si ya está instalado, ejecuta una vez (como admin):\n"
                "  py -m pywin32_postinstall -install")
        try:
            pythoncom.CoInitialize()
            self._app = win32com.client.Dispatch("Outlook.Application")
            self._ns = self._app.GetNamespace("MAPI")
        except Exception as exc:
            raise ErrorCorreo(
                f"No se pudo conectar a Outlook. Verifica que esté instalado y con tu "
                f"cuenta configurada. Detalle: {exc}") from exc
        perfil = ""
        try:
            perfil = self._app.Session.CurrentProfileName
        except Exception:
            pass
        log.info("Conectado a Outlook (perfil: %s).", perfil or "?")
        self.forzar_sincronizacion()

    def forzar_sincronizacion(self) -> None:
        """Fuerza a Outlook Clásico a sincronizar con Exchange (SendAndReceive + SyncObjects)."""
        if self._ns is None or not getattr(self.cfg, "forzar_sincronizacion_correo", True):
            return
        try:
            if getattr(self._ns, "Offline", False):
                log.warning(
                    "AVISO: Outlook Clásico está en modo 'Trabajar sin conexión' (Offline). "
                    "Desactívalo en la pestaña 'Enviar y recibir' de Outlook Clásico para recibir correos actuales."
                )
        except Exception:
            pass

        log.info("Forzando sincronización de Outlook Clásico con el servidor Exchange...")
        sincronizado = False
        try:
            self._ns.SendAndReceive(False)
            sincronizado = True
        except Exception as exc:
            log.debug("SendAndReceive no disponible: %s", exc)

        try:
            sync_objs = self._ns.SyncObjects
            for i in range(1, sync_objs.Count + 1):
                try:
                    sync_objs.Item(i).Start()
                    sincronizado = True
                except Exception:
                    continue
        except Exception as exc:
            log.debug("SyncObjects no disponible: %s", exc)

        if sincronizado:
            pausa = max(0.0, float(getattr(self.cfg, "pausa_sincronizacion_correo", 4.0)))
            if pausa > 0:
                log.info("Esperando %.1f s para descargar correos recientes...", pausa)
                time.sleep(pausa)
        else:
            log.warning("No fue posible disparar la sincronización automática de Outlook.")

    def desconectar(self) -> None:
        self._ns = None
        self._app = None
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # ---------------- diagnóstico ----------------
    def listar_buzones(self) -> List[str]:
        if self._ns is None:
            self.conectar()
        resultado = []
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                b = self._ns.Folders.Item(i)
                smtp = self._smtp_del_buzon(b)
                if smtp:
                    resultado.append(f"{smtp}   ({b.Name})")
                else:
                    resultado.append(b.Name)
            except Exception:
                pass
        return resultado

    def listar_carpetas(self) -> Dict[str, List[str]]:
        if self._ns is None:
            self.conectar()
        resultado: Dict[str, List[str]] = {}
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                buzon = self._ns.Folders.Item(i)
                carpetas = []
                for j in range(1, buzon.Folders.Count + 1):
                    carpetas.append(buzon.Folders.Item(j).Name)
                resultado[buzon.Name] = carpetas
            except Exception:
                pass
        return resultado

    def contar_altas_por_carpeta(self, desde: date, hasta: date,
                                 limite: int = 500) -> Dict[str, int]:
        """Para el diagnóstico: cuenta correos de altas en cada carpeta accesible."""
        if self._ns is None:
            self.conectar()
        resultado: Dict[str, int] = {}
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                buzon = self._ns.Folders.Item(i)
            except Exception:
                continue
            for j in range(1, buzon.Folders.Count + 1):
                try:
                    carpeta = buzon.Folders.Item(j)
                    filtro = self._filtro_fecha(desde, hasta, carpeta.Name)
                    try:
                        items = carpeta.Items.Restrict(filtro) if filtro else carpeta.Items
                    except Exception:
                        items = carpeta.Items
                    conteo = 0
                    for k in range(1, min(items.Count, limite) + 1):
                        try:
                            msg = items.Item(k)
                            fecha = self._fecha_mensaje(msg, carpeta.Name)
                            if fecha is None or not (desde <= fecha.date() <= hasta):
                                continue
                            es_alta, _ = self._es_correo_altas(msg)
                            if es_alta:
                                conteo += 1
                        except Exception:
                            pass
                    if conteo:
                        resultado[f"{buzon.Name} / {carpeta.Name}"] = conteo
                except Exception:
                    pass
        return resultado

    # ---------------- búsqueda principal ----------------
    MAX_CORREOS_POR_CARPETA = 150
    CORTE_FUERA_DE_RANGO = 20

    def _fecha_extremo(self, items, inicio: int, fin: int, paso: int, tipo_carpeta: str) -> Optional[datetime]:
        """Obtiene la primera fecha válida cerca de un extremo de la colección (máx 5 intentos)."""
        tope = 5
        revisados = 0
        idx = inicio
        while (paso > 0 and idx <= fin) or (paso < 0 and idx >= fin):
            revisados += 1
            if revisados > tope:
                break
            try:
                fecha = self._fecha_mensaje(items.Item(idx), tipo_carpeta)
                if fecha is not None:
                    return fecha
            except Exception:
                pass
            idx += paso
        return None

    def _indices_recientes(self, items, total: int, tipo_carpeta: str, limite: int) -> range:
        """Devuelve el rango de índices empezando SIEMPRE por los correos más recientes,
        acotado como máximo a `limite` elementos.
        """
        if total <= 1:
            return range(1, total + 1)
        f_primero = self._fecha_extremo(items, 1, total, 1, tipo_carpeta)
        f_ultimo = self._fecha_extremo(items, total, 1, -1, tipo_carpeta)
        if f_primero and f_ultimo and f_ultimo > f_primero:
            return range(total, max(0, total - limite), -1)
        return range(1, min(total, limite) + 1)

    def buscar_altas(self, desde: date, hasta: date,
                     max_correos: int = MAX_CORREOS_POR_CARPETA) -> List[CorreoAlta]:
        if self._ns is None:
            self.conectar()

        correos: List[CorreoAlta] = []
        vistos = set()

        for entrada in self.obtener_carpetas_busqueda():
            buzon_nombre, tipo_carpeta = (entrada + ["", ""])[:2]
            carpeta = self._obtener_carpeta(buzon_nombre, tipo_carpeta)
            if carpeta is None:
                log.warning("No se encontró la carpeta '%s' en el buzón '%s'.",
                            tipo_carpeta, buzon_nombre or "(actual)")
                continue

            try:
                items_base = carpeta.Items
                total_carpeta = items_base.Count
            except Exception as exc:
                log.warning("No se pudo abrir '%s/%s': %s",
                            buzon_nombre or "(actual)", tipo_carpeta, exc)
                continue

            if total_carpeta == 0:
                log.info("Carpeta '%s/%s' vacía.", buzon_nombre or "(actual)", tipo_carpeta)
                continue

            # Reset de stats por carpeta
            for k in self._stats:
                self._stats[k] = 0

            campo = "SentOn" if "sent" in tipo_carpeta.lower() else "ReceivedTime"

            # 1) Intentar acotar con Restrict al rango exacto [desde, hasta]
            items = items_base
            filtro = self._filtro_fecha(desde, hasta, tipo_carpeta)
            if filtro:
                try:
                    restringidos = items_base.Restrict(filtro)
                    if restringidos.Count > 0:
                        items = restringidos
                        log.info("Filtro Restrict en '%s/%s': %d correo(s) en [%s → %s] (de %d totales).",
                                 buzon_nombre or "(actual)", tipo_carpeta,
                                 items.Count, desde.strftime("%d.%m.%Y"),
                                 hasta.strftime("%d.%m.%Y"), total_carpeta)
                except Exception as exc:
                    log.debug("Restrict no aplicó en '%s/%s': %s", buzon_nombre, tipo_carpeta, exc)

            # 2) Ordenar por fecha descendente y detectar extremo más reciente
            try:
                items.Sort(f"[{campo}]", True)
                ordenado = True
            except Exception as exc:
                log.warning("No se pudo ordenar '%s' por %s: %s.", tipo_carpeta, campo, exc)
                ordenado = False

            total = items.Count
            indices = self._indices_recientes(items, total, tipo_carpeta, max_correos)
            log.info("Revisando hasta %d correo(s) recientes en '%s/%s' (rango %s → %s)...",
                     len(indices), buzon_nombre or "(actual)", tipo_carpeta,
                     desde.strftime("%d.%m.%Y"), hasta.strftime("%d.%m.%Y"))

            primera = ultima = None
            fuera_de_rango = 0
            altas_en_carpeta = 0

            for i in indices:
                try:
                    msg = items.Item(i)
                    self._stats["procesados"] += 1
                    fecha = self._fecha_mensaje(msg, tipo_carpeta)
                    if fecha is None:
                        continue
                    if primera is None:
                        primera = fecha

                    if fecha.date() > hasta:
                        continue

                    if fecha.date() < desde:
                        fuera_de_rango += 1
                        if ordenado and fuera_de_rango >= self.CORTE_FUERA_DE_RANGO:
                            break
                        continue
                    fuera_de_rango = 0
                    ultima = fecha

                    es_alta, remitente = self._es_correo_altas(msg)
                    if not es_alta:
                        continue

                    clave = (
                        (getattr(msg, "Subject", "") or "").strip().lower(),
                        remitente,
                        fecha.strftime("%Y%m%d%H%M"),
                    )
                    if clave in vistos:
                        continue
                    vistos.add(clave)

                    correos.append(self._convertir(msg, tipo_carpeta, remitente))
                    altas_en_carpeta += 1
                    if altas_en_carpeta <= 10:
                        log.info("  ✓ Alta #%d: %s | %s | %r",
                                 altas_en_carpeta,
                                 fecha.strftime("%d.%m.%Y %H:%M:%S"),
                                 remitente,
                                 (getattr(msg, "Subject", "") or "")[:90])
                except Exception as exc:
                    log.warning("Error procesando correo %d: %s: %s",
                                i, exc.__class__.__name__, exc)

            log.info("  Procesados: %d | Rango: %s → %s | Altas: %d",
                     self._stats["procesados"],
                     primera.strftime("%d.%m.%Y") if primera else "?",
                     ultima.strftime("%d.%m.%Y") if ultima else "?",
                     altas_en_carpeta)
            log.info("  Motivos de rechazo: "
                     "sin_asunto_alta=%d | sin_remitente=%d | "
                     "no_analista=%d | sin_caja=%d | aceptados=%d",
                     self._stats["sin_asunto_alta"],
                     self._stats["sin_remitente"],
                     self._stats["no_analista"],
                     self._stats["sin_caja"],
                     self._stats["aceptados"])

        correos.sort(key=lambda c: c.enviado, reverse=True)
        log.info("Correos de altas encontrados: %d", len(correos))
        return correos

    # ---------------- búsqueda de brokers (PDS) en lote ----------------
    MAX_CORREOS_BROKERS = 350

    def _identificar_broker_mensaje(self, msg) -> Tuple[str, str, str]:
        """Devuelve (broker_canonico, remitente_smtp, cuerpo_texto) si el correo
        pertenece a uno de los brokers del catálogo o fue reenviado (p. ej. por el Lic. Julio).
        """
        catalogo: Dict[str, List[str]] = getattr(self.cfg, "catalogo_brokers", {}) or {}
        if not catalogo:
            return "", "", ""

        remitente = self._direccion_remitente(msg).lower()
        nombre_rem = (getattr(msg, "SenderName", "") or "").lower()

        # 1) Match directo en remitente o nombre visible del remitente
        for broker, claves in catalogo.items():
            if any(c.lower() in remitente or c.lower() in nombre_rem for c in claves if c):
                try:
                    cuerpo = msg.Body or ""
                except Exception:
                    cuerpo = ""
                return broker, remitente, cuerpo

        # 2) Reenvíos (del Lic. Julio o reenvíos internos donde el correo del broker viene en el cuerpo)
        try:
            asunto = (getattr(msg, "Subject", "") or "").lower()
        except Exception:
            asunto = ""

        es_julio_o_reenvio = (
            "julio" in remitente
            or "julio" in nombre_rem
            or asunto.startswith(("rv:", "fw:", "fwd:", "re:"))
            or any(c.lower() in asunto for claves in catalogo.values() for c in claves if c)
        )
        if not es_julio_o_reenvio:
            return "", remitente, ""

        try:
            cuerpo = msg.Body or ""
        except Exception:
            cuerpo = ""
        texto_busqueda = f"{asunto}\n{cuerpo.lower()}"

        for broker, claves in catalogo.items():
            if any(c.lower() in texto_busqueda for c in claves if c):
                return broker, remitente, cuerpo

        return "", remitente, ""

    def buscar_correos_brokers(self, desde: date, hasta: date,
                               max_correos: int = MAX_CORREOS_BROKERS) -> List[CorreoBroker]:
        """Busca en lote correos de brokers (última semana) en las carpetas de entrada."""
        if self._ns is None:
            self.conectar()

        encontrados: List[CorreoBroker] = []
        vistos = set()

        # Revisar principalmente inbox (donde llegan los correos de brokers y reenvíos de Julio)
        entradas_todas = self.obtener_carpetas_busqueda()
        entradas_inbox = [e for e in entradas_todas if "inbox" in (e[1] if len(e) > 1 else "").lower()]
        carpetas_a_revisar = entradas_inbox or entradas_todas

        for entrada in carpetas_a_revisar:
            buzon_nombre, tipo_carpeta = (entrada + ["", ""])[:2]
            carpeta = self._obtener_carpeta(buzon_nombre, tipo_carpeta)
            if carpeta is None:
                continue

            try:
                items_base = carpeta.Items
                total_carpeta = items_base.Count
            except Exception as exc:
                log.debug("No se pudo abrir '%s/%s' para brokers: %s", buzon_nombre, tipo_carpeta, exc)
                continue

            if total_carpeta == 0:
                continue

            campo = "SentOn" if "sent" in tipo_carpeta.lower() else "ReceivedTime"
            items = items_base
            filtro = self._filtro_fecha(desde, hasta, tipo_carpeta)
            if filtro:
                try:
                    restringidos = items_base.Restrict(filtro)
                    if restringidos.Count > 0:
                        items = restringidos
                except Exception:
                    pass

            try:
                items.Sort(f"[{campo}]", True)
                ordenado = True
            except Exception:
                ordenado = False

            total = items.Count
            indices = self._indices_recientes(items, total, tipo_carpeta, max_correos)
            log.info("Buscando correos de brokers en '%s/%s' (%s → %s, máx %d correos)...",
                     buzon_nombre or "(actual)", tipo_carpeta,
                     desde.strftime("%d.%m.%Y"), hasta.strftime("%d.%m.%Y"), len(indices))

            fuera_de_rango = 0
            for i in indices:
                try:
                    msg = items.Item(i)
                    fecha = self._fecha_mensaje(msg, tipo_carpeta)
                    if fecha is None:
                        continue
                    if fecha.date() > hasta:
                        continue
                    if fecha.date() < desde:
                        fuera_de_rango += 1
                        if ordenado and fuera_de_rango >= self.CORTE_FUERA_DE_RANGO:
                            break
                        continue
                    fuera_de_rango = 0

                    broker, remitente, cuerpo = self._identificar_broker_mensaje(msg)
                    if not broker:
                        continue

                    asunto = getattr(msg, "Subject", "") or ""
                    clave = (broker, asunto.strip().lower(), fecha.strftime("%Y%m%d%H%M"))
                    if clave in vistos:
                        continue
                    vistos.add(clave)

                    adjuntos: List[str] = []
                    try:
                        for j in range(1, msg.Attachments.Count + 1):
                            adjuntos.append(msg.Attachments.Item(j).FileName)
                    except Exception:
                        pass

                    encontrados.append(CorreoBroker(
                        broker=broker,
                        asunto=asunto,
                        remitente=remitente,
                        enviado=fecha,
                        cuerpo_texto=cuerpo,
                        adjuntos=adjuntos,
                    ))
                except Exception as exc:
                    log.debug("Error leyendo correo %d en búsqueda de brokers: %s", i, exc)

        encontrados.sort(key=lambda c: c.enviado, reverse=True)
        log.info("Correos de brokers encontrados en la semana: %d", len(encontrados))
        return encontrados

    @staticmethod
    def _fecha_mensaje(msg, tipo_carpeta: str):
        """Fecha del mensaje, robusta ante distintos tipos de retorno de Outlook."""
        t = tipo_carpeta.lower()
        atributos = ("ReceivedTime", "SentOn", "CreationTime") if "inbox" in t \
                    else ("SentOn", "ReceivedTime", "CreationTime")
        for attr in atributos:
            try:
                valor = getattr(msg, attr)
                if isinstance(valor, datetime):
                    return valor
            except Exception:
                continue
        return None

    # ---------------- internos ----------------
    def obtener_carpetas_busqueda(self) -> List[List[str]]:
        """Determina automáticamente el buzón de analista en esta PC y devuelve
        los pares [buzon, 'sent'] y [buzon, 'inbox'] a revisar.
        """
        if self.cfg.carpetas_correo:
            return [list(e) for e in self.cfg.carpetas_correo]

        if self.cfg.buzon_correo:
            b = self.cfg.buzon_correo.strip()
            log.info("Usando buzón configurado explícitamente: %s (sent e inbox).", b)
            return [[b, "sent"], [b, "inbox"]]

        if self._ns is None:
            self.conectar()

        detectados: List[str] = []
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                buzon = self._ns.Folders.Item(i)
                smtp = (self._smtp_del_buzon(buzon) or "").lower().strip()
                nombre = (buzon.Name or "").lower().strip()
                for analista in self._analistas:
                    usuario_analista = analista.split("@")[0]
                    if (
                        (smtp and smtp == analista)
                        or analista in nombre
                        or (usuario_analista and (usuario_analista == nombre or usuario_analista in smtp or usuario_analista in nombre))
                    ):
                        identificador = smtp or buzon.Name
                        if identificador not in detectados:
                            detectados.append(identificador)
                            log.info("Buzón de analista seleccionado automáticamente en esta PC: %s", identificador)
                        break
            except Exception:
                continue

        if detectados:
            carpetas: List[List[str]] = []
            for b in detectados:
                carpetas.append([b, "sent"])
                carpetas.append([b, "inbox"])
            return carpetas

        log.info("No se identificó el buzón por nombre entre los 4 analistas; "
                 "se usará el buzón predeterminado de Outlook en esta PC (sent e inbox).")
        return [["", "sent"], ["", "inbox"]]

    def _obtener_carpeta(self, buzon_nombre: str, tipo_carpeta: str):
        try:
            # Si no se especifica buzón, usar el default de Outlook
            if not buzon_nombre:
                # Pero si hay un buzón configurado globalmente, respetarlo
                if self.cfg.buzon_correo:
                    buzon_nombre = self.cfg.buzon_correo
                else:
                    indice = _OL_FOLDERS.get(tipo_carpeta.lower())
                    if indice is None:
                        return None
                    return self._ns.GetDefaultFolder(indice)

            buzon = self._buscar_buzon(buzon_nombre)
            if buzon is None:
                return None

            nombres = _NOMBRES_CARPETA.get(tipo_carpeta.lower(), [tipo_carpeta])
            for j in range(1, buzon.Folders.Count + 1):
                f = buzon.Folders.Item(j)
                if f.Name in nombres:
                    return f
            return None
        except Exception as exc:
            log.debug("Error obteniendo carpeta '%s' de '%s': %s",
                      tipo_carpeta, buzon_nombre or "(actual)", exc)
            return None

    def _buscar_buzon(self, nombre: str):
        """Busca un buzón por SMTP o por nombre visible, en TODOS los buzones del perfil."""
        objetivo = (nombre or "").lower().strip()
        if not objetivo:
            return None

        # Pasada 1: coincidencia exacta por SMTP
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                buzon = self._ns.Folders.Item(i)
                smtp = self._smtp_del_buzon(buzon)
                if smtp and smtp.lower() == objetivo:
                    log.info("Buzón '%s' encontrado por SMTP.", objetivo)
                    return buzon
            except Exception:
                pass

        # Pasada 2: por nombre visible (tolera mayúsculas y variantes)
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                buzon = self._ns.Folders.Item(i)
                if objetivo in (buzon.Name or "").lower():
                    log.info("Buzón '%s' encontrado por nombre visible ('%s').",
                             objetivo, buzon.Name)
                    return buzon
            except Exception:
                pass

        log.warning("No se encontró el buzón '%s'. Buzones disponibles: %s",
                    objetivo, self._listar_smtp_buzones())
        return None

    def _smtp_del_buzon(self, buzon) -> str:
        """SMTP del dueño del buzón. Vacío si no se puede leer."""
        # 1) Del Store raíz
        try:
            root = buzon.Store.GetRootFolder()
            val = str(root.PropertyAccessor.GetProperty(_PR_SMTP_RECIP) or "")
            if "@" in val:
                return val
        except Exception:
            pass
        # 2) Del folder raíz del buzón
        try:
            val = str(buzon.PropertyAccessor.GetProperty(_PR_SMTP_RECIP) or "")
            if "@" in val:
                return val
        except Exception:
            pass
        # 3) Del Store (a veces está en PR_EMAIL_ADDRESS)
        try:
            val = str(buzon.Store.PropertyAccessor.GetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x3003001F") or "")
            if "@" in val:
                return val
        except Exception:
            pass
        return ""

    def _listar_smtp_buzones(self) -> list:
        smtps = []
        for i in range(1, self._ns.Folders.Count + 1):
            try:
                b = self._ns.Folders.Item(i)
                smtps.append(self._smtp_del_buzon(b) or b.Name)
            except Exception:
                pass
        return smtps

    def _filtro_fecha(self, desde: date, hasta: date, tipo_carpeta: str) -> str:
        t = (tipo_carpeta or "").lower()
        campo = "SentOn" if ("sent" in t or "enviado" in t) else "ReceivedTime"
        fin = hasta + timedelta(days=1)
        return (f"[{campo}] >= '{desde.strftime('%d/%m/%Y')} 00:00' "
                f"AND [{campo}] < '{fin.strftime('%d/%m/%Y')} 00:00'")


    def _direccion_remitente(self, msg) -> str:
        """SMTP del remitente. Cachea por DN para evitar lookups lentos a Exchange."""
        # 1) Fast path: para remitentes externos, PidTagSenderEmailAddress ya es SMTP
        dn = ""
        try:
            val = str(msg.PropertyAccessor.GetProperty(_PR_SMTP_SENDER) or "")
            if "@" in val:
                return val
            dn = val  # DN de Exchange; lo usamos como clave de caché
        except Exception:
            pass

        # 2) Cache hit → instantáneo
        if dn and dn in self._cache_smtp:
            return self._cache_smtp[dn]

        # 3) PidTagSmtpAddress también puede tener el SMTP
        try:
            val = str(msg.PropertyAccessor.GetProperty(_PR_SMTP_RECIP) or "")
            if "@" in val:
                if dn:
                    self._cache_smtp[dn] = val
                return val
        except Exception:
            pass

        # 4) SenderEmailAddress: SMTP o DN según el tipo
        addr = ""
        try:
            addr = str(msg.SenderEmailAddress or "")
            if "@" in addr:
                return addr
        except Exception:
            pass
        if addr and addr in self._cache_smtp:
            return self._cache_smtp[addr]

        # 5) Slow path: ExchangeUser (puede tardar; se cachea incluso el fallo "")
        resultado = ""
        try:
            if msg.SenderEmailType == "EX":
                u = msg.Sender.GetExchangeUser()
                if u:
                    resultado = u.PrimarySmtpAddress or ""
        except Exception as exc:
            log.debug("ExchangeUser falló: %s", exc)

        clave = dn or addr
        if clave:
            self._cache_smtp[clave] = resultado
        return resultado

    @staticmethod
    def _direccion_destinatario(recipient) -> str:
        try:
            val = str(recipient.PropertyAccessor.GetProperty(_PR_SMTP_RECIP) or "")
            if "@" in val:
                return val
        except Exception:
            pass
        try:
            if getattr(recipient, "AddressType", "") == "EX":
                u = recipient.AddressEntry.GetExchangeUser()
                if u and u.PrimarySmtpAddress and "@" in u.PrimarySmtpAddress:
                    return u.PrimarySmtpAddress
        except Exception:
            pass
        try:
            addr = getattr(recipient, "Address", "") or ""
            if "@" in addr:
                return addr
        except Exception:
            pass
        return ""

    def _convertir(self, msg, tipo_carpeta: str, remitente_email: str = "") -> CorreoAlta:
        if not remitente_email:
            remitente_email = self._direccion_remitente(msg)
        remitente_nombre = getattr(msg, "SenderName", "") or ""

        destinatarios: List[str] = []
        for i in range(1, msg.Recipients.Count + 1):
            d = self._direccion_destinatario(msg.Recipients.Item(i))
            if d:
                destinatarios.append(d)

        adjuntos: List[str] = []
        try:
            for i in range(1, msg.Attachments.Count + 1):
                adjuntos.append(msg.Attachments.Item(i).FileName)
        except Exception:
            pass

        try:
            cuerpo = msg.Body or ""
        except Exception:
            cuerpo = ""

        fecha = self._fecha_mensaje(msg, tipo_carpeta) or datetime.now()

        nombre_firma, cargo_firma = _extraer_firma(cuerpo)
        operador_canonico = _identificar_operador(nombre_firma)
        if nombre_firma:
            log.debug("  Firma: %r (%s) → operador=%r",
                      nombre_firma, cargo_firma, operador_canonico)

        return CorreoAlta(
            asunto=getattr(msg, "Subject", "") or "",
            remitente_nombre=remitente_nombre,
            remitente_email=remitente_email,
            enviado=fecha,
            destinatarios=destinatarios,
            cuerpo_texto=cuerpo,
            adjuntos=adjuntos,
            carpeta_origen=tipo_carpeta,
            operador_firma=nombre_firma,
            cargo_firma=cargo_firma,
            operador=operador_canonico,
        )

    def _es_caja(self, direccion: str) -> bool:
        """True si la dirección corresponde a un buzón de cajas.

        Tolera sufijos como '.hdc': 'admcaja1@x' coincide con 'admcaja1.hdc@x'.
        """
        if not direccion or "@" not in direccion:
            return False
        usuario, _, dominio = direccion.lower().partition("@")
        for caja in self._cajas:
            c_usuario, _, c_dominio = caja.lower().partition("@")
            if not c_usuario:
                continue
            if dominio != c_dominio:
                continue
            if usuario == c_usuario or usuario.startswith(c_usuario + "."):
                return True
        return False

    def _es_correo_altas(self, msg) -> Tuple[bool, str]:
        """Devuelve (es_alta, remitente_smtp). No usa regex: solo substring."""
        # Filtro rápido por asunto (substring en mayúsculas)
        try:
            asunto = (getattr(msg, "Subject", "") or "").upper()
        except Exception:
            asunto = ""
        if "ALTA" not in asunto:
            self._stats["sin_asunto_alta"] += 1
            return False, ""

        # Remitente (cacheado: puede tardar la primera vez por DN)
        remitente = self._direccion_remitente(msg).lower()
        if not remitente:
            self._stats["sin_remitente"] += 1
            log.debug("  ✗ Sin remitente. Asunto: %r", asunto[:80])
            return False, ""
        if not any(a in remitente for a in self._analistas if a):
            self._stats["no_analista"] += 1
            log.debug("  ✗ Remitente '%s' no es analista. Asunto: %r",
                      remitente, asunto[:80])
            return False, remitente

        # Destinatarios
        for i in range(1, msg.Recipients.Count + 1):
            try:
                rec = msg.Recipients.Item(i)
                d = self._direccion_destinatario(rec)
                if self._es_caja(d):
                    self._stats["aceptados"] += 1
                    log.debug("  ✓ Alta: %s → %s | %r", remitente, d, asunto[:80])
                    return True, remitente
            except Exception:
                continue

        self._stats["sin_caja"] += 1
        log.debug("  ✗ Remitente '%s' es analista pero sin caja. Asunto: %r",
                  remitente, asunto[:80])
        return False, remitente

