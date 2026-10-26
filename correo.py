"""Lectura de correos desde Outlook de escritorio (win32com).

Requiere Windows + Outlook instalado con la cuenta configurada.
No requiere Azure ni permisos de admin: usa la sesión de Outlook.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
import unicodedata

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
                    items = carpeta.Items.Restrict(filtro)
                    conteo = 0
                    for k in range(1, min(items.Count, limite) + 1):
                        try:
                            if self._es_correo_altas(items.Item(k)):
                                conteo += 1
                        except Exception:
                            pass
                    if conteo:
                        resultado[f"{buzon.Name} / {carpeta.Name}"] = conteo
                except Exception:
                    pass
        return resultado

    # ---------------- búsqueda principal ----------------
    def buscar_altas(self, desde: date, hasta: date) -> List[CorreoAlta]:
        if self._ns is None:
            self.conectar()

        correos: List[CorreoAlta] = []
        vistos = set()

        for entrada in self.cfg.carpetas_correo:
            buzon_nombre, tipo_carpeta = (entrada + ["", ""])[:2]
            carpeta = self._obtener_carpeta(buzon_nombre, tipo_carpeta)
            if carpeta is None:
                log.warning("No se encontró la carpeta '%s' en el buzón '%s'.",
                            tipo_carpeta, buzon_nombre or "(actual)")
                continue

            try:
                items = carpeta.Items
                total = items.Count
            except Exception as exc:
                log.warning("No se pudo abrir '%s/%s': %s",
                            buzon_nombre or "(actual)", tipo_carpeta, exc)
                continue

            log.info("Revisando %d correo(s) en '%s/%s'...",
                     total, buzon_nombre or "(actual)", tipo_carpeta)
            if total == 0:
                continue

            # Reset de stats por carpeta
            for k in self._stats:
                self._stats[k] = 0

            campo = "SentOn" if "sent" in tipo_carpeta.lower() else "ReceivedTime"
            try:
                items.Sort(f"[{campo}]", True)
                ordenado = True
            except Exception as exc:
                log.warning("No se pudo ordenar '%s' por %s: %s. Se iterará completo.",
                            tipo_carpeta, campo, exc)
                ordenado = False

            primera = ultima = None
            fuera_de_rango = 0
            altas_en_carpeta = 0

            for i in range(1, total + 1):
                try:
                    msg = items.Item(i)
                    self._stats["procesados"] += 1
                    fecha = self._fecha_mensaje(msg, tipo_carpeta)
                    if fecha is None:
                        continue
                    if primera is None:
                        primera = fecha

                    if fecha.date() < desde:
                        fuera_de_rango += 1
                        if ordenado and fuera_de_rango >= 100:
                            break
                        continue
                    fuera_de_rango = 0

                    if fecha.date() > hasta:
                        continue
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
                    # A nivel WARNING para que NUNCA se oculte
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
        
        return ""


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

