# -*- coding: utf-8 -*-
"""
Interfaz gráfica (CustomTkinter) — Rediseño Dark Mode profesional.

Arquitectura:
    VentanaPrincipal (CTk)
    ├── Sidebar        → navegación animada (colapsable)
    └── Área principal
        ├── TopBar     → título de página + píldora de estado
        └── Host       → Panel | Resultados | Ajustes

Notas de UX:
  * Sistema de elevación: BG (raíz) < SIDEBAR < CARD < CARD_HOVER.
  * Las tarjetas reaccionan al hover con un chequeo de "pointer still inside"
    para evitar el parpadeo clásico de Tk al entrar en un hijo.
  * El sidebar se anima con `after()` (easing exponencial), nunca bloquea.
  * Resultado: en lugar de un Toplevel, se muestra en la página "Resultados"
    y se copia automáticamente al portapapeles.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path
from tkinter import TclError, filedialog, messagebox
from typing import Callable, List, Optional

import customtkinter as ctk

log = logging.getLogger("UI")

# =============================================================================
#  DEPENDENCIAS DEL PROYECTO ORIGINAL  (stubs si no están disponibles)
# =============================================================================
try:  # pragma: no cover
    from carpetas import parsear_fecha          # noqa: F401
    from excepciones import ErrorCarpeta        # noqa: F401
    from interaccion import Interaccion         # noqa: F401
    from modelos import DatosManuales           # noqa: F401
    from version import VERSION                 # noqa: F401

    DEPENDENCIAS_REALES = True
except Exception:  # pragma: no cover
    DEPENDENCIAS_REALES = False
    VERSION = "2.0"

    class ErrorCarpeta(Exception):
        """Error de validación de carpeta / fecha."""

    def parsear_fecha(texto: str) -> date:
        try:
            return datetime.strptime((texto or "").strip(), "%d.%m.%Y").date()
        except ValueError as exc:
            raise ErrorCarpeta("Formato de fecha inválido. Usa dd.mm.aaaa") from exc

    class DatosManuales:
        CAMPOS = ("operador", "broker", "vobo", "hora_cajas", "respuesta_hab")

        def __init__(self, **kw):
            for campo in self.CAMPOS:
                setattr(self, campo, kw.get(campo) or "N/A")

    class Interaccion:
        def estado(self, mensaje, nivel="ok"): ...
        def progreso(self, actual, total): ...
        def pedir_datos_manuales(self, *a, **k): return DatosManuales()
        def elegir_opcion(self, *a, **k): return 0
        def elegir_varias(self, *a, **k): return [0]
        def pedir_archivo(self, *a, **k): return None
        def confirmar(self, *a, **k): return True
        def mostrar_resultado(self, *a, **k): ...
        def terminado(self, *a, **k): ...


# =============================================================================
#  SISTEMA DE DISEÑO  (tokens)
# =============================================================================
class T:
    """Tokens de diseño — paleta por elevación."""

    # --- Superficies (de más oscura a más elevada) ---
    BG                 = "#0A0B0F"   # raíz, casi negro
    SIDEBAR            = "#0E1016"   # panel lateral
    CARD               = "#141822"   # tarjeta base
    CARD_HOVER         = "#1B2030"   # tarjeta elevada (hover)
    CARD_BORDER        = "#212736"
    CARD_BORDER_HOVER  = "#313A55"
    INSET              = "#0E111A"   # pozos: inputs, textbox, barras

    # --- Acento ---
    ACCENT        = "#7B6CFF"
    ACCENT_HOVER  = "#948AFF"
    ACCENT_SOFT   = "#241F45"
    ACCENT_TEXT   = "#CFC8FF"

    # --- Texto ---
    TEXT       = "#E8EAF2"
    TEXT_MUTED = "#8B93A8"
    TEXT_DIM   = "#565E73"

    # --- Semánticos ---
    OK    = "#3DDC97"
    WARN  = "#F5A524"
    ERROR = "#FF5C7C"
    INFO  = "#7B8BFF"

    # --- Geometría ---
    RADIUS    = 16
    RADIUS_SM = 10
    PAD       = 22


COLORES = {"ok": T.OK, "aviso": T.WARN, "error": T.ERROR, "info": T.INFO}

TITULOS = {
    "panel":      ("Panel de control", "Supervisa y lanza la digitalización de expedientes."),
    "resultados": ("Resultados",       "Filas generadas listas para pegar en la bitácora."),
    "ajustes":    ("Ajustes",          "Preferencias de la aplicación y del robot."),
}

_PILL = {"ok": "En curso", "info": "Trabajando", "aviso": "Atención", "error": "Error"}


def fuente(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """Fuente unificada (Segoe UI con fallback del sistema)."""
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


# =============================================================================
#  COMPONENTES BASE
# =============================================================================
class Card(ctk.CTkFrame):
    """Tarjeta con elevación y hover (cambio de fondo + borde)."""

    def __init__(self, master, *, hover: bool = True, padding: int = T.PAD, **kwargs):
        kwargs.setdefault("fg_color", T.CARD)
        kwargs.setdefault("corner_radius", T.RADIUS)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", T.CARD_BORDER)
        super().__init__(master, **kwargs)

        self._hover_on   = hover
        self._bg_base    = kwargs["fg_color"]
        self._bd_base    = kwargs["border_color"]
        self._bg_hover   = T.CARD_HOVER
        self._bd_hover   = T.CARD_BORDER_HOVER
        self._job_leave  = None
        self._hover_listo = False

        # Contenedor interno con padding generoso
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=padding, pady=padding)

    # -- API ---------------------------------------------------------------
    def bind_hover(self) -> None:
        """Llamar DESPUÉS de construir el contenido de la tarjeta."""
        if not self._hover_on or self._hover_listo:
            return
        self._hover_listo = True
        self._recorrer(self)

    # -- Interno -----------------------------------------------------------
    def _recorrer(self, widget) -> None:
        widget.bind("<Enter>", self._entrar, add="+")
        widget.bind("<Leave>", self._salir,  add="+")
        for hijo in widget.winfo_children():
            self._recorrer(hijo)

    def _entrar(self, _e=None) -> None:
        if self._job_leave is not None:
            try:
                self.after_cancel(self._job_leave)
            except TclError:
                pass
            self._job_leave = None
        self.configure(fg_color=self._bg_hover, border_color=self._bd_hover)

    def _salir(self, _e=None) -> None:
        if self._job_leave is not None:
            try:
                self.after_cancel(self._job_leave)
            except TclError:
                pass
        # Pequeño debounce: si el puntero sigue dentro, no se revierte
        self._job_leave = self.after(45, self._comprobar_salida)

    def _comprobar_salida(self) -> None:
        self._job_leave = None
        try:
            bajo = self.winfo_containing(*self.winfo_pointerxy())
        except TclError:
            bajo = None
        while bajo is not None:
            if bajo is self:
                return
            bajo = getattr(bajo, "master", None)
        self.configure(fg_color=self._bg_base, border_color=self._bd_base)


class StatCard(Card):
    """Tarjeta de métrica: título pequeño, valor grande y pie."""

    def __init__(self, master, titulo: str, valor: str = "—", pie: str = "",
                 acento: str = T.TEXT):
        super().__init__(master, padding=18)
        ctk.CTkLabel(self.body, text=titulo.upper(), font=fuente(10, "bold"),
                     text_color=T.TEXT_DIM, anchor="w").pack(anchor="w")
        self.lbl_valor = ctk.CTkLabel(self.body, text=valor, font=fuente(26, "bold"),
                                      text_color=acento, anchor="w")
        self.lbl_valor.pack(anchor="w", pady=(6, 0))
        self.lbl_pie = ctk.CTkLabel(self.body, text=pie, font=fuente(11),
                                    text_color=T.TEXT_MUTED, anchor="w")
        self.lbl_pie.pack(anchor="w")
        self.bind_hover()

    def set(self, valor: Optional[str] = None, pie: Optional[str] = None,
            acento: Optional[str] = None) -> None:
        if valor is not None:
            self.lbl_valor.configure(text=valor)
        if pie is not None:
            self.lbl_pie.configure(text=pie)
        if acento is not None:
            self.lbl_valor.configure(text_color=acento)


class StatusPill(ctk.CTkFrame):
    """Píldora de estado con punto de color."""

    def __init__(self, master, texto: str = "En espera", nivel: str = "info"):
        super().__init__(master, fg_color=T.INSET, corner_radius=20)
        self._punto = ctk.CTkLabel(self, text="●", font=fuente(10),
                                   text_color=COLORES.get(nivel, T.INFO), width=12)
        self._punto.pack(side="left", padx=(14, 6), pady=7)
        self._texto = ctk.CTkLabel(self, text=texto, font=fuente(11, "bold"),
                                   text_color=T.TEXT_MUTED)
        self._texto.pack(side="left", padx=(0, 15), pady=7)

    def set(self, texto: str, nivel: str = "info") -> None:
        self._punto.configure(text_color=COLORES.get(nivel, T.INFO))
        self._texto.configure(text=texto)


class NavItem(ctk.CTkButton):
    """Ítem de navegación que se adapta al estado colapsado/expandido."""

    def __init__(self, master, icono: str, texto: str, comando: Callable[[], None]):
        super().__init__(master, text=f"  {icono}   {texto}", anchor="w", height=44,
                         corner_radius=T.RADIUS_SM, fg_color="transparent",
                         hover_color=T.CARD_HOVER, text_color=T.TEXT_MUTED,
                         font=fuente(13, "bold"), command=comando)
        self.icono = icono
        self.texto = texto

    def marcar(self, activo: bool) -> None:
        self.configure(fg_color=T.ACCENT_SOFT if activo else "transparent",
                       text_color=T.TEXT if activo else T.TEXT_MUTED)

    def colapsar(self, colapsado: bool) -> None:
        self.configure(text=f"  {self.icono}" if colapsado
                       else f"  {self.icono}   {self.texto}")


# =============================================================================
#  SIDEBAR ANIMADO
# =============================================================================
class Sidebar(ctk.CTkFrame):
    """Menú lateral colapsable con animación no bloqueante vía after()."""

    ANCHO_COLAPSADO = 78
    ANCHO_EXPANDIDO = 240
    PASO_MS         = 15
    FACTOR_EASING   = 0.28

    def __init__(self, master, al_navegar: Callable[[str], None]):
        super().__init__(master, fg_color=T.SIDEBAR, corner_radius=0,
                         width=self.ANCHO_EXPANDIDO)
        self.pack_propagate(False)
        self.grid_propagate(False)

        self._al_navegar = al_navegar
        self._expandido  = True
        self._animando   = False
        self._objetivo   = float(self.ANCHO_EXPANDIDO)
        self._actual     = float(self.ANCHO_EXPANDIDO)
        self._items: dict[str, NavItem] = {}

        self._construir()

    # -- Construcción ------------------------------------------------------
    def _construir(self) -> None:
        # Marca
        marca = ctk.CTkFrame(self, fg_color="transparent")
        marca.pack(fill="x", padx=16, pady=(22, 20))

        ctk.CTkLabel(marca, text="◈", width=46, height=46, corner_radius=14,
                     fg_color=T.ACCENT_SOFT, text_color=T.ACCENT_TEXT,
                     font=fuente(20, "bold")).pack(side="left")

        self._marca_txt = ctk.CTkFrame(marca, fg_color="transparent")
        self._marca_txt.pack(side="left", padx=12)
        ctk.CTkLabel(self._marca_txt, text="ECH Robot", font=fuente(15, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(self._marca_txt, text=f"Versión {VERSION}", font=fuente(11),
                     text_color=T.TEXT_DIM, anchor="w").pack(anchor="w")

        # Sección
        self._seccion = ctk.CTkLabel(self, text="MENÚ", font=fuente(10, "bold"),
                                     text_color=T.TEXT_DIM, anchor="w")
        self._seccion.pack(fill="x", padx=26, pady=(4, 8))

        # Ítems
        for clave, icono, texto in (("panel", "◧", "Panel"),
                                    ("resultados", "▤", "Resultados"),
                                    ("ajustes", "⚙", "Ajustes")):
            item = NavItem(self, icono, texto, lambda c=clave: self._al_navegar(c))
            item.pack(fill="x", padx=14, pady=3)
            self._items[clave] = item

        # Empujar el toggle al fondo
        ctk.CTkFrame(self, fg_color="transparent").pack(fill="both", expand=True)

        self._btn_toggle = ctk.CTkButton(
            self, text="  ☰   Contraer", anchor="w", height=42,
            corner_radius=T.RADIUS_SM, fg_color="transparent",
            hover_color=T.CARD_HOVER, text_color=T.TEXT_MUTED,
            font=fuente(12, "bold"), command=self.alternar)
        self._btn_toggle.pack(fill="x", padx=14, pady=(0, 16))

    # -- API ---------------------------------------------------------------
    def marcar(self, nombre: str) -> None:
        for clave, item in self._items.items():
            item.marcar(clave == nombre)

    def alternar(self) -> None:
        self._expandido = not self._expandido
        self._objetivo = float(self.ANCHO_COLAPSADO if not self._expandido
                               else self.ANCHO_EXPANDIDO)
        self._aplicar_textos(self._expandido)
        if not self._animando:
            self._animando = True
            self.after(self.PASO_MS, self._paso_animacion)

    # -- Animación ---------------------------------------------------------
    def _paso_animacion(self) -> None:
        delta = self._objetivo - self._actual
        if abs(delta) < 1.0:
            self._actual = self._objetivo
            self.configure(width=int(self._actual))
            self._animando = False
            return
        self._actual += delta * self.FACTOR_EASING
        self.configure(width=int(self._actual))
        self.after(self.PASO_MS, self._paso_animacion)

    def _aplicar_textos(self, expandido: bool) -> None:
        for item in self._items.values():
            item.colapsar(not expandido)
        if expandido:
            if not self._marca_txt.winfo_manager():
                self._marca_txt.pack(side="left", padx=12)
            self._seccion.configure(text="MENÚ")
            self._btn_toggle.configure(text="  ☰   Contraer")
        else:
            self._marca_txt.pack_forget()
            self._seccion.configure(text="")
            self._btn_toggle.configure(text="  ☰")


# =============================================================================
#  TOP BAR
# =============================================================================
class TopBar(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent", height=86)
        self.pack_propagate(False)

        izq = ctk.CTkFrame(self, fg_color="transparent")
        izq.pack(side="left", padx=30, pady=18)

        self.lbl_titulo = ctk.CTkLabel(izq, text="Panel de control",
                                       font=fuente(21, "bold"), text_color=T.TEXT,
                                       anchor="w")
        self.lbl_titulo.pack(anchor="w")
        self.lbl_sub = ctk.CTkLabel(izq, text="", font=fuente(12),
                                    text_color=T.TEXT_MUTED, anchor="w")
        self.lbl_sub.pack(anchor="w", pady=(2, 0))

        self.pill = StatusPill(self, "En espera", "info")
        self.pill.pack(side="right", padx=30)

    def set_titulo(self, titulo: str, subtitulo: str = "") -> None:
        self.lbl_titulo.configure(text=titulo)
        self.lbl_sub.configure(text=subtitulo)


# =============================================================================
#  VENTANA PRINCIPAL
# =============================================================================
class VentanaPrincipal(ctk.CTk):
    """Ventana raíz: Sidebar animado + TopBar + Host de páginas."""

    def __init__(self, crear_robot: Callable[[str, date, "Interaccion"], object]):
        super().__init__(fg_color=T.BG)
        self._crear_robot = crear_robot
        self._en_curso = False
        self._tsv = ""

        self.title(f"Robot Digitalizador ECH · v{VERSION}")
        self.minsize(1060, 700)
        self._centrar(1200, 760)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Sidebar ---
        self.sidebar = Sidebar(self, self._navegar)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        # --- Área principal ---
        area = ctk.CTkFrame(self, fg_color="transparent")
        area.grid(row=0, column=1, sticky="nsew")
        area.grid_columnconfigure(0, weight=1)
        area.grid_rowconfigure(1, weight=1)

        self.topbar = TopBar(area)
        self.topbar.grid(row=0, column=0, sticky="ew")

        self.host = ctk.CTkFrame(area, fg_color="transparent")
        self.host.grid(row=1, column=0, sticky="nsew", padx=30, pady=(0, 28))
        self.host.grid_columnconfigure(0, weight=1)
        self.host.grid_rowconfigure(0, weight=1)

        self.paginas: dict[str, ctk.CTkFrame] = {}
        self._construir_panel()
        self._construir_resultados()
        self._construir_ajustes()

        self._navegar("panel")
        self._log("Interfaz lista. Esperando operador.")

        # Atajo: Ctrl+Enter lanza la digitalización
        self.bind("<Control-Return>", lambda _e: self.arrancar_robot())

    # ------------------------------------------------------------------ geo
    def _centrar(self, ancho: int, alto: int) -> None:
        self.update_idletasks()
        x = (self.winfo_screenwidth() - ancho) // 2
        y = (self.winfo_screenheight() - alto) // 2
        self.geometry(f"{ancho}x{alto}+{x}+{y}")

    # -------------------------------------------------------------- páginas
    def _navegar(self, nombre: str) -> None:
        for clave, pagina in self.paginas.items():
            if clave == nombre:
                pagina.grid(row=0, column=0, sticky="nsew")
            else:
                pagina.grid_forget()
        self.sidebar.marcar(nombre)
        titulo, sub = TITULOS.get(nombre, ("", ""))
        self.topbar.set_titulo(titulo, sub)

    # ----------------------------------------------------------- PAGE: panel
    def _construir_panel(self) -> None:
        pagina = ctk.CTkFrame(self.host, fg_color="transparent")
        pagina.grid_columnconfigure(0, weight=1)
        pagina.grid_rowconfigure(1, weight=1)
        self.paginas["panel"] = pagina

        # --- Métricas ---
        fila = ctk.CTkFrame(pagina, fg_color="transparent")
        fila.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        for i in range(3):
            fila.grid_columnconfigure(i, weight=1, uniform="met")

        self.stat_expedientes = StatCard(fila, "Expedientes", "—", "sin procesar")
        self.stat_expedientes.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        self.stat_filas = StatCard(fila, "Filas generadas", "0", "listas para pegar")
        self.stat_filas.grid(row=0, column=1, sticky="nsew", padx=(0, 14))

        self.stat_estado = StatCard(fila, "Estado", "En espera",
                                    "el robot no ha corrido", T.TEXT_MUTED)
        self.stat_estado.grid(row=0, column=2, sticky="nsew")

        # --- Cuerpo ---
        cuerpo = ctk.CTkFrame(pagina, fg_color="transparent")
        cuerpo.grid(row=1, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=3)
        cuerpo.grid_columnconfigure(1, weight=2)
        cuerpo.grid_rowconfigure(0, weight=1)

        self._card_digitalizacion(cuerpo)
        self._card_actividad(cuerpo)

    def _card_digitalizacion(self, master) -> None:
        card = Card(master)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        b = card.body

        ctk.CTkLabel(b, text="Nueva digitalización", font=fuente(17, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(b, text="Introduce la fecha con la que se darán de alta "
                             "los expedientes.",
                     font=fuente(12), text_color=T.TEXT_MUTED, anchor="w",
                     justify="left", wraplength=460).pack(anchor="w", pady=(3, 22))

        # Fecha + botón "Hoy"
        fila_fecha = ctk.CTkFrame(b, fg_color="transparent")
        fila_fecha.pack(fill="x", pady=(0, 20))

        self.entrada_fecha = ctk.CTkEntry(
            fila_fecha, width=220, height=46, corner_radius=T.RADIUS_SM,
            fg_color=T.INSET, border_color=T.CARD_BORDER, border_width=1,
            text_color=T.TEXT, font=fuente(14),
            placeholder_text="dd.mm.aaaa", placeholder_text_color=T.TEXT_DIM)
        self.entrada_fecha.insert(0, date.today().strftime("%d.%m.%Y"))
        self.entrada_fecha.pack(side="left")

        ctk.CTkButton(fila_fecha, text="Hoy", width=74, height=46,
                      corner_radius=T.RADIUS_SM, fg_color="transparent",
                      border_width=1, border_color=T.CARD_BORDER,
                      hover_color=T.CARD_HOVER, text_color=T.TEXT_MUTED,
                      font=fuente(12, "bold"),
                      command=self._poner_hoy).pack(side="left", padx=10)

        # Botón principal
        self.boton_iniciar = ctk.CTkButton(
            b, text="▶   Iniciar digitalización", height=50,
            corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
            hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
            font=fuente(14, "bold"), command=self.arrancar_robot)
        self.boton_iniciar.pack(fill="x", pady=(0, 24))

        # Progreso
        cab = ctk.CTkFrame(b, fg_color="transparent")
        cab.pack(fill="x")
        ctk.CTkLabel(cab, text="PROGRESO", font=fuente(10, "bold"),
                     text_color=T.TEXT_DIM).pack(side="left")
        self.lbl_pct = ctk.CTkLabel(cab, text="0 %", font=fuente(11, "bold"),
                                    text_color=T.TEXT_MUTED)
        self.lbl_pct.pack(side="right")

        self.barra_progreso = ctk.CTkProgressBar(
            b, height=10, corner_radius=6, progress_color=T.ACCENT,
            fg_color=T.INSET)
        self.barra_progreso.pack(fill="x", pady=(8, 20))
        self.barra_progreso.set(0)

        # Estado
        caja_estado = ctk.CTkFrame(b, fg_color=T.INSET, corner_radius=T.RADIUS_SM)
        caja_estado.pack(fill="x")

        self._punto_estado = ctk.CTkLabel(caja_estado, text="●", font=fuente(11),
                                          text_color=T.INFO)
        self._punto_estado.pack(side="left", padx=(14, 8), pady=13)

        self.label_estado = ctk.CTkLabel(caja_estado, text="Esperando operador...",
                                         font=fuente(12), text_color=T.TEXT_MUTED,
                                         anchor="w", justify="left", wraplength=430)
        self.label_estado.pack(side="left", padx=(0, 14), pady=13)

        card.bind_hover()

    def _card_actividad(self, master) -> None:
        card = Card(master)
        card.grid(row=0, column=1, sticky="nsew")
        b = card.body

        cab = ctk.CTkFrame(b, fg_color="transparent")
        cab.pack(fill="x")
        ctk.CTkLabel(cab, text="Actividad", font=fuente(15, "bold"),
                     text_color=T.TEXT).pack(side="left")
        ctk.CTkButton(cab, text="Limpiar", width=68, height=28,
                      corner_radius=8, fg_color="transparent",
                      hover_color=T.CARD_HOVER, text_color=T.TEXT_DIM,
                      font=fuente(11, "bold"),
                      command=self._limpiar_log).pack(side="right")

        self.log_box = ctk.CTkTextbox(b, fg_color=T.INSET, corner_radius=T.RADIUS_SM,
                                      border_width=0, text_color=T.TEXT_MUTED,
                                      font=("Consolas", 11), wrap="word")
        self.log_box.pack(fill="both", expand=True, pady=(12, 0))
        self.log_box.configure(state="disabled")

        card.bind_hover()

    # ------------------------------------------------------ PAGE: resultados
    def _construir_resultados(self) -> None:
        pagina = ctk.CTkFrame(self.host, fg_color="transparent")
        self.paginas["resultados"] = pagina

        card = Card(pagina)
        card.pack(fill="both", expand=True)
        b = card.body

        cab = ctk.CTkFrame(b, fg_color="transparent")
        cab.pack(fill="x")
        izq = ctk.CTkFrame(cab, fg_color="transparent")
        izq.pack(side="left", anchor="w")
        ctk.CTkLabel(izq, text="Filas para pegar en la bitácora",
                     font=fuente(17, "bold"), text_color=T.TEXT,
                     anchor="w").pack(anchor="w")
        ctk.CTkLabel(izq, text="Pégalas en la primera celda libre de la columna B "
                               "(Episodio). Ya están copiadas al portapapeles.",
                     font=fuente(12), text_color=T.TEXT_MUTED, anchor="w",
                     justify="left", wraplength=620).pack(anchor="w", pady=(3, 0))

        self.lbl_filas = ctk.CTkLabel(cab, text="", font=fuente(12, "bold"),
                                      text_color=T.OK)
        self.lbl_filas.pack(side="right", anchor="e")

        self.caja_resultado = ctk.CTkTextbox(b, fg_color=T.INSET,
                                             corner_radius=T.RADIUS_SM,
                                             border_width=0, text_color=T.TEXT,
                                             font=("Consolas", 12), wrap="none")
        self.caja_resultado.pack(fill="both", expand=True, pady=(16, 14))
        self.caja_resultado.insert("1.0", "Aún no hay filas generadas.\n\n"
                                          "Lanza una digitalización desde el Panel "
                                          "para ver aquí el resultado.")
        self.caja_resultado.configure(state="disabled")

        pie = ctk.CTkFrame(b, fg_color="transparent")
        pie.pack(fill="x")
        self.btn_copiar = ctk.CTkButton(
            pie, text="Copiar de nuevo", height=42, corner_radius=T.RADIUS_SM,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
            font=fuente(12, "bold"), command=self._copiar_resultado)
        self.btn_copiar.pack(side="left")

        ctk.CTkButton(pie, text="Volver al panel", height=42,
                      corner_radius=T.RADIUS_SM, fg_color="transparent",
                      border_width=1, border_color=T.CARD_BORDER,
                      hover_color=T.CARD_HOVER, text_color=T.TEXT_MUTED,
                      font=fuente(12, "bold"),
                      command=lambda: self._navegar("panel")).pack(side="left", padx=10)

        card.bind_hover()

    # --------------------------------------------------------- PAGE: ajustes
    def _construir_ajustes(self) -> None:
        pagina = ctk.CTkFrame(self.host, fg_color="transparent")
        self.paginas["ajustes"] = pagina

        card = Card(pagina)
        card.pack(fill="x")
        b = card.body

        ctk.CTkLabel(b, text="Preferencias", font=fuente(17, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w", pady=(0, 14))

        self._fila_switch(b, "Modo oscuro", "Paleta de elevación optimizada para "
                                            "sesiones largas.", True)
        self._fila_switch(b, "Animaciones de interfaz", "Transiciones del menú "
                                                        "lateral y del progreso.", True)
        self._fila_switch(b, "Copiar resultado automáticamente",
                          "Copia el TSV al portapapeles al terminar la corrida.", True)

        card.bind_hover()

        acerca = Card(pagina)
        acerca.pack(fill="x", pady=(18, 0))
        b2 = acerca.body
        ctk.CTkLabel(b2, text="Acerca de", font=fuente(15, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(b2, text=f"Robot Digitalizador ECH · versión {VERSION}\n"
                              f"Dependencias reales: "
                              f"{'detectadas' if DEPENDENCIAS_REALES else 'modo demo'}",
                     font=fuente(12), text_color=T.TEXT_MUTED, anchor="w",
                     justify="left").pack(anchor="w", pady=(6, 0))
        acerca.bind_hover()

    def _fila_switch(self, master, titulo: str, subtitulo: str, activo: bool):
        fila = ctk.CTkFrame(master, fg_color="transparent")
        fila.pack(fill="x", pady=9)

        txt = ctk.CTkFrame(fila, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(txt, text=titulo, font=fuente(13, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(txt, text=subtitulo, font=fuente(11),
                     text_color=T.TEXT_MUTED, anchor="w").pack(anchor="w")

        sw = ctk.CTkSwitch(fila, text="", width=46, progress_color=T.ACCENT,
                           button_color="#FFFFFF", button_hover_color="#E4E4E4",
                           fg_color=T.INSET)
        sw.pack(side="right")
        if activo:
            sw.select()
        return sw

    # ---------------------------------------------------------------- lógica
    def _poner_hoy(self) -> None:
        self.entrada_fecha.delete(0, "end")
        self.entrada_fecha.insert(0, date.today().strftime("%d.%m.%Y"))

    def arrancar_robot(self) -> None:
        if self._en_curso:
            return
        try:
            fecha = parsear_fecha(self.entrada_fecha.get())
        except ErrorCarpeta as exc:
            self.set_estado(str(exc), "error")
            return

        self._en_curso = True
        self.set_estado("Procesando expedientes, por favor espera...", "ok")
        self.topbar.pill.set("En curso", "ok")
        self.boton_iniciar.configure(state="disabled", text="Procesando…")
        self.stat_estado.set("En curso", "trabajando", T.WARN)
        self.stat_expedientes.set("0", "procesando…", T.INFO)
        self._set_pct(0.0)

        self.barra_progreso.configure(mode="indeterminate")
        self.barra_progreso.start()

        threading.Thread(target=self._hilo_robot, args=(fecha,),
                         daemon=True, name="hilo-robot").start()

    def _hilo_robot(self, fecha: date) -> None:
        # El operador real ya no se pregunta: se obtiene del correo de cada alta.
        try:
            robot = self._crear_robot("", fecha, InteraccionUI(self))
            robot.ejecutar()
        except Exception as exc:  # noqa: BLE001
            log.exception("Fallo del robot")
            self.set_estado(f"Error inesperado: {exc}", "error")
            self.finalizar(False)

    # -------------------------------------------------------- API para la UI
    def set_estado(self, mensaje: str, nivel: str = "ok") -> None:
        color = COLORES.get(nivel, T.INFO)
        self.label_estado.configure(text=mensaje, text_color=T.TEXT)
        self._punto_estado.configure(text_color=color)
        self.topbar.pill.set(_PILL.get(nivel, "Trabajando"), nivel)
        self._log(mensaje)

    def set_progreso(self, actual: int, total: int) -> None:
        self.barra_progreso.stop()
        self.barra_progreso.configure(mode="determinate")
        fraccion = max(0.0, min(1.0, actual / total)) if total > 0 else 0.0
        self.barra_progreso.set(fraccion)
        self._set_pct(fraccion)
        self.stat_expedientes.set(f"{actual}/{total}", "procesados", T.INFO)

    def mostrar_tsv(self, tsv: str, total: int) -> None:
        self._tsv = tsv
        self.caja_resultado.configure(state="normal")
        self.caja_resultado.delete("1.0", "end")
        self.caja_resultado.insert("1.0", tsv)
        self.caja_resultado.configure(state="disabled")

        self.stat_filas.set(str(total), "filas nuevas", T.OK)
        self.lbl_filas.configure(text=f"{total} fila(s) nueva(s)")
        self._copiar_portapapeles(tsv)
        self._navegar("resultados")

    def finalizar(self, exito: bool) -> None:
        self._en_curso = False
        self.barra_progreso.stop()
        self.barra_progreso.configure(mode="determinate")
        if exito:
            self.barra_progreso.set(1)
            self._set_pct(1.0)
            self.boton_iniciar.configure(text="Cerrar robot", state="normal",
                                         command=self.destroy)
            self.stat_estado.set("Completado", "corrida terminada", T.OK)
            self.topbar.pill.set("Completado", "ok")
            self._log("Corrida finalizada correctamente.")
        else:
            self.barra_progreso.set(0)
            self._set_pct(0.0)
            self.boton_iniciar.configure(text="▶   Iniciar digitalización",
                                         state="normal", command=self.arrancar_robot)
            self.stat_estado.set("Error", "revisa la actividad", T.ERROR)
            self.topbar.pill.set("Error", "error")

    # ------------------------------------------------------------- utilidades
    def _set_pct(self, fraccion: float) -> None:
        self.lbl_pct.configure(text=f"{int(round(fraccion * 100))} %")

    def _limpiar_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, mensaje: str) -> None:
        if not hasattr(self, "log_box"):
            return
        hora = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{hora}]  {mensaje}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _copiar_portapapeles(self, texto: str) -> bool:
        try:
            self.clipboard_clear()
            self.clipboard_append(texto)
            self.update_idletasks()
            return True
        except TclError as exc:
            log.warning("No se pudo copiar al portapapeles: %s", exc)
            return False

    def _copiar_resultado(self) -> None:
        if not self._tsv:
            return
        if self._copiar_portapapeles(self._tsv):
            self.btn_copiar.configure(text="¡Copiado!")
            self.after(1500, lambda: self.btn_copiar.configure(text="Copiar de nuevo"))


# =============================================================================
#  PUENTE HILO ROBOT ⇄ HILO TKINTER
# =============================================================================
class InteraccionUI(Interaccion):
    """Puente entre el hilo del robot y el hilo de la interfaz."""

    def __init__(self, app: VentanaPrincipal):
        self.app = app
        self._omitir_faltantes = False

    def _en_hilo_principal(self, funcion):
        """Ejecuta `funcion` en el hilo de tkinter y espera su resultado."""
        resultado: dict = {}
        listo = threading.Event()

        def ejecutar():
            try:
                resultado["valor"] = funcion()
            except Exception as exc:  # se relanza en el hilo del robot
                resultado["error"] = exc
            finally:
                listo.set()

        self.app.after(0, ejecutar)
        listo.wait()
        if "error" in resultado:
            raise resultado["error"]
        return resultado.get("valor")

    # --- Implementación de la interfaz Interaccion -----------------------
    def estado(self, mensaje: str, nivel: str = "ok") -> None:
        self.app.after(0, lambda: self.app.set_estado(mensaje, nivel))

    def progreso(self, actual: int, total: int) -> None:
        self.app.after(0, lambda: self.app.set_progreso(actual, total))

    def pedir_datos_manuales(self, paciente: str, episodio: str, modo: str,
                             broker_sugerido: str = "",
                             sugeridos: Optional[DatosManuales] = None,
                             pedir_operador: bool = False) -> DatosManuales:
        def abrir():
            popup = VentanaPreguntas(self.app, paciente, episodio, modo,
                                     broker_sugerido, sugeridos,
                                     pedir_operador=pedir_operador)
            self.app.wait_window(popup)
            return popup.datos

        datos = self._en_hilo_principal(abrir)
        if datos is None:
            log.warning("Ventana cerrada sin guardar: se usará 'N/A' en los datos manuales.")
            return DatosManuales()
        return datos

    def elegir_opcion(self, titulo: str, mensaje: str, opciones: List[str],
                      sugerida: int) -> Optional[int]:
        def abrir():
            ventana = VentanaOpciones(self.app, titulo, mensaje, opciones, sugerida)
            self.app.wait_window(ventana)
            return ventana.indice

        return self._en_hilo_principal(abrir)

    def elegir_varias(self, titulo: str, mensaje: str,
                      opciones: List[str]) -> Optional[List[int]]:
        def abrir():
            ventana = VentanaVarias(self.app, titulo, mensaje, opciones)
            self.app.wait_window(ventana)
            return ventana.indices

        return self._en_hilo_principal(abrir)

    def pedir_archivo(self, titulo: str, mensaje: str,
                      carpeta_inicial: Path) -> Optional[Path]:
        if self._omitir_faltantes:
            log.info("Faltante omitido automáticamente (el operador eligió omitir todos).")
            return None

        def abrir():
            ventana = VentanaFaltante(self.app, titulo, mensaje, carpeta_inicial)
            self.app.wait_window(ventana)
            return ventana.ruta, ventana.omitir_todos

        ruta, omitir_todos = self._en_hilo_principal(abrir)
        if omitir_todos:
            self._omitir_faltantes = True
        return ruta

    def confirmar(self, titulo: str, mensaje: str) -> bool:
        return bool(self._en_hilo_principal(
            lambda: messagebox.askyesno(titulo, mensaje, parent=self.app)))

    def mostrar_resultado(self, tsv: str, total: int) -> None:
        # El resultado ya no es un Toplevel: ahora se integra como página.
        self.app.after(0, lambda: self.app.mostrar_tsv(tsv, total))

    def terminado(self, exito: bool) -> None:
        self.app.after(0, lambda: self.app.finalizar(exito))


# =============================================================================
#  HELPERS COMUNES PARA POPUPS
# =============================================================================
def _centrar_sobre(ventana: ctk.CTkToplevel, master, ancho: int, alto: int) -> None:
    master.update_idletasks()
    x = master.winfo_x() + master.winfo_width() // 2 - ancho // 2
    y = master.winfo_y() + master.winfo_height() // 2 - alto // 2
    ventana.geometry(f"{ancho}x{alto}+{x}+{y}")


def _tomar_foco_modal(ventana: ctk.CTkToplevel) -> None:
    try:
        ventana.lift()
        ventana.focus_force()
        ventana.grab_set()
    except TclError:
        log.debug("No se pudo tomar el foco del pop-up (ventana aún no visible).")


# =============================================================================
#  POPUP · DATOS MANUALES
# =============================================================================
class VentanaPreguntas(ctk.CTkToplevel):
    """Pop-up para capturar los datos manuales de la bitácora.

    modo: 'completo' | 'particular' | 'hemo' | 'simple'
    """

    _CAMPOS = {
        "operador":      ("Operador que trabajó el alta:",      "Ej. Karina, Paty, Alberto..."),
        "broker":        ("Broker (PDS):",                       "Ej. GNP, N/A..."),
        "vobo":          ("Quién dio VoBo (Supervisor):",        "Ej. MARIO, N/A..."),
        "hora_cajas":    ("Hora mandada a cajas:",               "Ej. 14:30..."),
        "respuesta_hab": ("Quién contestó en habitación:",       "Ej. PX 15:00..."),
    }
    _POR_MODO = {
        "completo":   ["broker", "vobo", "hora_cajas", "respuesta_hab"],
        "particular": ["vobo", "hora_cajas", "respuesta_hab"],
        "hemo":       ["vobo", "hora_cajas"],
        "simple":     ["vobo", "hora_cajas"],
    }

    def __init__(self, master, paciente: str, episodio: str, modo: str = "completo",
                 broker_sugerido: str = "",
                 sugeridos: Optional[DatosManuales] = None,
                 pedir_operador: bool = False):
        super().__init__(master, fg_color=T.BG)
        self.title("Faltan datos")
        self.datos: Optional[DatosManuales] = None

        claves_base = list(self._POR_MODO.get(modo, self._POR_MODO["completo"]))
        self._claves = (["operador"] + claves_base) if pedir_operador else claves_base
        self._entradas: dict[str, ctk.CTkEntry] = {}

        ancho = 460
        alto = 250 + 92 * len(self._claves)
        _centrar_sobre(self, master, ancho, alto)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        b = card.body

        # Cabecera
        ctk.CTkLabel(b, text="Faltan datos manuales", font=fuente(18, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")

        etiqueta_modo = {"hemo": "  (Hemodiálisis: solo se llama a cajas)",
                         "particular": "  (Particular)"}.get(modo, "")
        info = f"Paciente: {paciente}\nEpisodio: {episodio}{etiqueta_modo}"
        ctk.CTkLabel(b, text=info, font=fuente(12),
                     text_color=T.ACCENT_TEXT, anchor="w", justify="left",
                     wraplength=ancho - 80).pack(anchor="w", pady=(4, 18))

        # Campos
        for numero, clave in enumerate(self._claves, start=1):
            titulo, ejemplo = self._CAMPOS[clave]
            ctk.CTkLabel(b, text=f"{numero}. {titulo}", font=fuente(11, "bold"),
                         text_color=T.TEXT_MUTED, anchor="w").pack(anchor="w",
                                                                   pady=(4, 2))
            entrada = ctk.CTkEntry(
                b, placeholder_text=ejemplo, height=42,
                corner_radius=T.RADIUS_SM, fg_color=T.INSET,
                border_width=1, border_color=T.CARD_BORDER,
                text_color=T.TEXT, font=fuente(13),
                placeholder_text_color=T.TEXT_DIM,
            )
            entrada.pack(fill="x", pady=(0, 4))

            # Precarga
            valor = ""
            if sugeridos is not None:
                valor = getattr(sugeridos, clave, "") or ""
            if clave == "broker" and broker_sugerido and not valor:
                valor = broker_sugerido
            if valor and valor.strip().upper() != "N/A":
                entrada.insert(0, valor)

            entrada.bind("<Return>", lambda _e, c=clave: self._al_enter(c))
            self._entradas[clave] = entrada

        self.lbl_error = ctk.CTkLabel(b, text="", text_color=T.ERROR,
                                      font=fuente(12, "bold"))
        self.lbl_error.pack(pady=(8, 6))

        ctk.CTkButton(b, text="Guardar y continuar (Enter)", height=44,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
                      font=fuente(13, "bold"),
                      command=self.guardar_datos).pack(fill="x")

        self.bind("<Return>", lambda _e: self.guardar_datos())
        self.after(80, self._tomar_foco)

    def _tomar_foco(self) -> None:
        _tomar_foco_modal(self)
        # Enfocar el primer campo vacío (o el último si todos tienen valor)
        for clave in self._claves:
            if not self._entradas[clave].get().strip():
                self._entradas[clave].focus_set()
                return
        if self._claves:
            self._entradas[self._claves[-1]].focus_set()

    def _al_enter(self, clave_actual: str) -> None:
        for clave in self._claves:
            if not self._entradas[clave].get().strip():
                if clave != clave_actual or not self._entradas[clave_actual].get().strip():
                    self._entradas[clave].focus_set()
                    return
        self.guardar_datos()

    def guardar_datos(self) -> None:
        valores = {c: e.get().strip() for c, e in self._entradas.items()}
        if not all(valores.values()):
            self.lbl_error.configure(text="¡Atención: por favor llena todos los campos!")
            return
        self.datos = DatosManuales(**valores)   # los no preguntados quedan en "N/A"
        self.destroy()


# =============================================================================
#  POPUP · ELEGIR UNA OPCIÓN
# =============================================================================
class VentanaOpciones(ctk.CTkToplevel):
    def __init__(self, master, titulo: str, mensaje: str,
                 opciones: List[str], sugerida: int):
        super().__init__(master, fg_color=T.BG)
        self.title(titulo)
        self.indice: Optional[int] = None

        ancho = 640
        alto = 230 + 52 * len(opciones)
        _centrar_sobre(self, master, ancho, alto)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        b = card.body

        ctk.CTkLabel(b, text=titulo, font=fuente(16, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(b, text=mensaje, font=fuente(12), text_color=T.TEXT_MUTED,
                     wraplength=ancho - 80, justify="left",
                     anchor="w").pack(anchor="w", pady=(4, 14))

        self._var = ctk.IntVar(value=sugerida)
        for i, texto in enumerate(opciones):
            sufijo = "   ·  sugerido" if i == sugerida else ""
            ctk.CTkRadioButton(
                b, text=texto + sufijo, variable=self._var, value=i,
                font=fuente(12), text_color=T.TEXT,
                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                border_color=T.CARD_BORDER,
                radiobutton_width=18, radiobutton_height=18,
            ).pack(anchor="w", padx=4, pady=6)

        ctk.CTkButton(b, text="Usar el seleccionado", height=44,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
                      font=fuente(13, "bold"),
                      command=self._aceptar).pack(fill="x", pady=(18, 0))

        self.bind("<Return>", lambda _e: self._aceptar())
        self.after(80, lambda: _tomar_foco_modal(self))

    def _aceptar(self) -> None:
        self.indice = self._var.get()
        self.destroy()


# =============================================================================
#  POPUP · ELEGIR VARIAS
# =============================================================================
class VentanaVarias(ctk.CTkToplevel):
    """Marcar varios archivos (p. ej. los 2 o 3 estados de cuenta de una separación)."""

    def __init__(self, master, titulo: str, mensaje: str, opciones: List[str]):
        super().__init__(master, fg_color=T.BG)
        self.title(titulo)
        self.indices: Optional[List[int]] = None

        ancho = 640
        alto = 230 + 52 * len(opciones)
        _centrar_sobre(self, master, ancho, alto)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        b = card.body

        ctk.CTkLabel(b, text=titulo, font=fuente(16, "bold"),
                     text_color=T.TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(b, text=mensaje, font=fuente(12), text_color=T.TEXT_MUTED,
                     wraplength=ancho - 80, justify="left",
                     anchor="w").pack(anchor="w", pady=(4, 14))

        self._vars: list[ctk.BooleanVar] = []
        for texto in opciones:
            var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(
                b, text=texto, variable=var, font=fuente(12), text_color=T.TEXT,
                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                border_color=T.CARD_BORDER, checkmark_color="#FFFFFF",
                checkbox_width=18, checkbox_height=18,
            ).pack(anchor="w", padx=4, pady=6)
            self._vars.append(var)

        ctk.CTkButton(b, text="Usar los marcados", height=44,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
                      font=fuente(13, "bold"),
                      command=self._aceptar).pack(fill="x", pady=(18, 0))

        self.bind("<Return>", lambda _e: self._aceptar())
        self.after(80, lambda: _tomar_foco_modal(self))

    def _aceptar(self) -> None:
        self.indices = [i for i, v in enumerate(self._vars) if v.get()] or None
        self.destroy()


# =============================================================================
#  POPUP · FALTANTE
# =============================================================================
class VentanaFaltante(ctk.CTkToplevel):
    """Un documento no se encontró: seleccionarlo, omitirlo u omitir todos."""

    def __init__(self, master, titulo: str, mensaje: str, carpeta_inicial: Path):
        super().__init__(master, fg_color=T.BG)
        self.title(titulo)
        self.ruta: Optional[Path] = None
        self.omitir_todos = False
        self._carpeta = carpeta_inicial

        ancho, alto = 540, 400
        _centrar_sobre(self, master, ancho, alto)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        b = card.body

        # Cabecera con icono
        cab = ctk.CTkFrame(b, fg_color="transparent")
        cab.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(cab, text="⚠", font=fuente(24, "bold"),
                     text_color=T.WARN, width=32).pack(side="left")
        ctk.CTkLabel(cab, text=titulo, font=fuente(16, "bold"),
                     text_color=T.TEXT, anchor="w").pack(side="left", padx=(8, 0))

        ctk.CTkLabel(b, text=mensaje, font=fuente(12), text_color=T.TEXT_MUTED,
                     wraplength=ancho - 80, justify="left",
                     anchor="w").pack(anchor="w", pady=(0, 20))

        ctk.CTkButton(b, text="Seleccionar archivo…", height=46,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color="#FFFFFF",
                      font=fuente(13, "bold"),
                      command=self._seleccionar).pack(fill="x", pady=(0, 10))

        ctk.CTkButton(b, text="Omitir (agregarlo manualmente después)",
                      height=42, corner_radius=T.RADIUS_SM, fg_color="transparent",
                      border_width=1, border_color=T.CARD_BORDER,
                      hover_color=T.CARD_HOVER, text_color=T.TEXT_MUTED,
                      font=fuente(12, "bold"),
                      command=self.destroy).pack(fill="x", pady=(0, 10))

        ctk.CTkButton(b, text="Omitir todos los faltantes de esta corrida",
                      height=42, corner_radius=T.RADIUS_SM, fg_color="transparent",
                      border_width=1, border_color=T.CARD_BORDER,
                      hover_color=T.CARD_HOVER, text_color=T.TEXT_DIM,
                      font=fuente(12, "bold"),
                      command=self._omitir_todos).pack(fill="x")

        self.after(80, lambda: _tomar_foco_modal(self))

    def _seleccionar(self) -> None:
        elegido = filedialog.askopenfilename(
            parent=self, initialdir=str(self._carpeta),
            title="Selecciona el documento",
            filetypes=[("Documentos", "*.pdf *.txt *.xls *.xlsx"),
                       ("Todos los archivos", "*.*")])
        if elegido:
            self.ruta = Path(elegido)
            self.destroy()

    def _omitir_todos(self) -> None:
        self.omitir_todos = True
        self.destroy()


# =============================================================================
#  ROBOT DEMO  (para que el archivo sea ejecutable tal cual)
# =============================================================================
class RobotDemo:
    """Simula una corrida completa del robot: progreso + popup + TSV."""

    def __init__(self, operador: str, fecha: date, interaccion: Interaccion):
        self._interaccion = interaccion
        self._fecha = fecha

    def ejecutar(self) -> None:
        self._interaccion.estado(
            f"Iniciando digitalización del {self._fecha.strftime('%d.%m.%Y')}…", "ok")
        time.sleep(0.6)

        total = 5
        pacientes = ["GARCÍA, ANA", "LÓPEZ, LUIS", "MARTÍNEZ, SOFÍA",
                     "HERNÁNDEZ, JOSÉ", "RUIZ, CARLA"]

        for i in range(total):
            self._interaccion.estado(
                f"Procesando expediente {i + 1}/{total}: {pacientes[i]}", "ok")
            self._interaccion.progreso(i + 1, total)
            time.sleep(0.5)

        # Pedir datos manuales del último expediente (bloquea hasta que el
        # operador los capture en el popup).
        datos = self._interaccion.pedir_datos_manuales(
            pacientes[-1], "EP-1024", "completo", broker_sugerido="GNP")

        # Construir TSV con columnas típicas de la bitácora
        filas = []
        for i, pac in enumerate(pacientes):
            filas.append("\t".join([
                str(i + 1),
                f"EP-102{i}",
                pac,
                self._fecha.strftime("%d.%m.%Y"),
                datos.operador,
                datos.broker,
                datos.vobo,
                f"1{5 + i}:{i * 7:02d}",
                datos.hora_cajas,
                datos.respuesta_hab,
                "OK",
                "N/A",
            ]))
        tsv = "\n".join(filas)

        self._interaccion.estado("Corrida completada. Resultados disponibles.", "ok")
        self._interaccion.mostrar_resultado(tsv, len(filas))
        self._interaccion.terminado(True)


def crear_robot_demo(operador: str, fecha: date, interaccion: Interaccion):
    """Punto de inyección: reemplaza por tu fábrica real si tienes los módulos."""
    if DEPENDENCIAS_REALES:
        # from robot import Robot
        # return Robot(operador, fecha, interaccion)
        pass
    return RobotDemo(operador, fecha, interaccion)


# =============================================================================
#  ENTRY POINT
# =============================================================================
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-8s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")   # base; los tokens T sobreescriben

    app = VentanaPrincipal(crear_robot_demo)
    app.mainloop()


if __name__ == "__main__":
    main()
                            