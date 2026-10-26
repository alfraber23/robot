"""Interfaz gráfica (customtkinter). Es la única parte que conoce tkinter."""
import logging
import threading
from datetime import date
from pathlib import Path
from tkinter import TclError, filedialog, messagebox
from typing import Callable, List, Optional

import customtkinter as ctk

from carpetas import parsear_fecha
from excepciones import ErrorCarpeta
from interaccion import Interaccion
from modelos import DatosManuales
from version import VERSION

log = logging.getLogger("UI")

COLORES = {"ok": "#00ff00", "aviso": "#f39c12", "error": "red", "info": "gray"}


class VentanaPrincipal(ctk.CTk):
    def __init__(self, crear_robot: Callable[[str, date, Interaccion], object]):
        super().__init__()
        self._crear_robot = crear_robot

        self.title(f"Robot Digitalizador ECH v{VERSION}")
        self.geometry("450x340")
        self.eval("tk::PlaceWindow . center")

        ctk.CTkLabel(self, text="Panel de Control",
                     font=("Roboto", 24, "bold")).pack(pady=(30, 15))

        ctk.CTkLabel(self, text="Fecha de las altas (dd.mm.aaaa):",
                     font=("Roboto", 12)).pack(pady=(0, 2))
        self.entrada_fecha = ctk.CTkEntry(self, width=250)
        self.entrada_fecha.insert(0, date.today().strftime("%d.%m.%Y"))
        self.entrada_fecha.pack(pady=(0, 15))

        self.boton_iniciar = ctk.CTkButton(self, text="Iniciar Digitalización",
                                           font=("Roboto", 14),
                                           command=self.arrancar_robot)
        self.boton_iniciar.pack(pady=10)

        self.barra_progreso = ctk.CTkProgressBar(self, mode="indeterminate", width=300)
        self.barra_progreso.pack(pady=10)
        self.barra_progreso.set(0)

        self.label_estado = ctk.CTkLabel(self, text="Esperando operador...",
                                         text_color="gray", wraplength=400)
        self.label_estado.pack(pady=10)

    def arrancar_robot(self) -> None:
        try:
            fecha = parsear_fecha(self.entrada_fecha.get())
        except ErrorCarpeta as exc:
            self.label_estado.configure(text=str(exc), text_color=COLORES["error"])
            return

        self.label_estado.configure(text="Procesando expedientes, por favor espera...",
                                    text_color=COLORES["ok"])
        self.boton_iniciar.configure(state="disabled")
        self.barra_progreso.start()
        self.update()

        threading.Thread(target=self._hilo_robot, args=(fecha,), daemon=True,
                         name="hilo-robot").start()

    def _hilo_robot(self, fecha: date) -> None:
        # El operador real ya no se pregunta: se obtiene del correo de cada alta.
        robot = self._crear_robot("", fecha, InteraccionUI(self))
        robot.ejecutar()

    def finalizar(self, exito: bool) -> None:
        """Reactiva los controles cuando el robot termina."""
        self.barra_progreso.stop()
        self.barra_progreso.configure(mode="determinate")
        if exito:
            self.barra_progreso.set(1)
            self.boton_iniciar.configure(text="Cerrar Robot", state="normal", command=self.destroy)
        else:
            self.barra_progreso.set(0)
            self.boton_iniciar.configure(text="Iniciar Digitalización", state="normal",
                                         command=self.arrancar_robot)

    def actualizar_progreso(self, actual: int, total: int) -> None:
        """Cambia la barra a modo determinista y muestra el avance real."""
        self.barra_progreso.stop()
        self.barra_progreso.configure(mode="determinate")
        fraccion = max(0.0, min(1.0, actual / total)) if total > 0 else 0.0
        self.barra_progreso.set(fraccion)


class InteraccionUI(Interaccion):
    """Puente entre el hilo del robot y el hilo de la interfaz."""

    def __init__(self, app: VentanaPrincipal):
        self.app = app
        self._omitir_faltantes = False

    def _en_hilo_principal(self, funcion):
        """Ejecuta `funcion` en el hilo de tkinter y espera su resultado."""
        resultado = {}
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

    def estado(self, mensaje: str, nivel: str = "ok") -> None:
        color = COLORES.get(nivel, COLORES["info"])
        self.app.after(0, lambda: self.app.label_estado.configure(text=mensaje, text_color=color))

    def progreso(self, actual: int, total: int) -> None:
        self.app.after(0, lambda: self.app.actualizar_progreso(actual, total))

    def pedir_datos_manuales(self, paciente: str, episodio: str, modo: str,
                             broker_sugerido: str = "",
                             sugeridos: Optional[DatosManuales] = None,
                             pedir_operador: bool = False) -> DatosManuales:
        def abrir():
            popup = VentanaPreguntas(self.app, paciente, episodio, modo,
                                     broker_sugerido, sugeridos, pedir_operador=pedir_operador)
            self.app.wait_window(popup)
            return popup.datos

        datos = self._en_hilo_principal(abrir)
        if datos is None:
            log.warning("Ventana cerrada sin guardar: se usará 'N/A' en los datos manuales.")
            return DatosManuales()
        return datos

    def elegir_opcion(self, titulo: str, mensaje: str, opciones: List[str], sugerida: int) -> Optional[int]:
        def abrir():
            ventana = VentanaOpciones(self.app, titulo, mensaje, opciones, sugerida)
            self.app.wait_window(ventana)
            return ventana.indice

        return self._en_hilo_principal(abrir)

    def pedir_archivo(self, titulo: str, mensaje: str, carpeta_inicial: Path) -> Optional[Path]:
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
        self.app.after(0, lambda: VentanaResultado(self.app, tsv, total))

    def terminado(self, exito: bool) -> None:
        self.app.after(0, lambda: self.app.finalizar(exito))


class VentanaResultado(ctk.CTkToplevel):
    """Filas nuevas listas para pegar en el Excel web (se copian solas al abrir)."""

    def __init__(self, master, tsv: str, total: int):
        super().__init__(master)
        self.title("Filas para pegar en la bitácora")
        self.geometry("820x420")
        self._tsv = tsv
        self.after(100, self.lift)

        ctk.CTkLabel(
            self, font=("Roboto", 14, "bold"),
            text=f"{total} fila(s) nueva(s). Pégalas en la primera celda libre de la columna B (Episodio).",
        ).pack(pady=(15, 5))
        ctk.CTkLabel(
            self, font=("Roboto", 12), text_color="gray",
            text="Ya están copiadas al portapapeles. No cierres el robot hasta haberlas pegado.",
        ).pack(pady=(0, 10))

        self.caja = ctk.CTkTextbox(self, width=780, height=250, wrap="none", font=("Consolas", 12))
        self.caja.pack(padx=20, pady=5)
        self.caja.insert("1.0", tsv)
        self.caja.configure(state="disabled")

        self.boton = ctk.CTkButton(self, text="Copiar de nuevo", command=self.copiar)
        self.boton.pack(pady=10)
        self.copiar()

    def copiar(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._tsv)
            self.update()
            self.boton.configure(text="¡Copiado!")
            self.after(1500, lambda: self.boton.configure(text="Copiar de nuevo"))
        except TclError as exc:
            log.warning("No se pudo copiar al portapapeles: %s", exc)
            self.boton.configure(text="No se pudo copiar: selecciona el texto a mano")


class VentanaOpciones(ctk.CTkToplevel):
    """Elegir uno entre varios archivos."""

    def __init__(self, master, titulo: str, mensaje: str, opciones: List[str], sugerida: int):
        super().__init__(master)
        self.title(titulo)
        self.indice: Optional[int] = None
        self.geometry(f"700x{260 + 45 * len(opciones)}")
        self.after(100, self._tomar_foco)

        ctk.CTkLabel(self, text=mensaje, font=("Roboto", 13, "bold"), wraplength=650,
                     justify="left").pack(padx=20, pady=(20, 10), anchor="w")
        self._var = ctk.IntVar(value=sugerida)
        for i, texto in enumerate(opciones):
            sugerido = "  (sugerido)" if i == sugerida else ""
            ctk.CTkRadioButton(self, text=texto + sugerido, variable=self._var, value=i,
                               font=("Roboto", 12)).pack(padx=25, pady=6, anchor="w")
        ctk.CTkButton(self, text="Usar el seleccionado", command=self._aceptar).pack(pady=20)
        self.bind("<Return>", lambda _e: self._aceptar())

    def _tomar_foco(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except TclError:
            pass

    def _aceptar(self) -> None:
        self.indice = self._var.get()
        self.destroy()


class VentanaFaltante(ctk.CTkToplevel):
    """Un documento no se encontró: seleccionarlo a mano, omitirlo u omitir todos los faltantes."""

    def __init__(self, master, titulo: str, mensaje: str, carpeta_inicial: Path):
        super().__init__(master)
        self.title(titulo)
        self.geometry("520x320")
        self.ruta: Optional[Path] = None
        self.omitir_todos = False
        self._carpeta = carpeta_inicial
        self.after(100, self._tomar_foco)

        ctk.CTkLabel(self, text=titulo, font=("Roboto", 16, "bold"), text_color="#f39c12").pack(pady=(20, 8))
        ctk.CTkLabel(self, text=mensaje, font=("Roboto", 13), wraplength=470, justify="center").pack(pady=(0, 15))
        ctk.CTkButton(self, text="Seleccionar archivo...", command=self._seleccionar).pack(pady=5)
        ctk.CTkButton(self, text="Omitir (agregarlo manualmente después)", fg_color="gray",
                      command=self.destroy).pack(pady=5)
        ctk.CTkButton(self, text="Omitir todos los faltantes de esta corrida", fg_color="gray",
                      command=self._omitir_todos).pack(pady=5)

    def _tomar_foco(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except TclError:
            pass

    def _seleccionar(self) -> None:
        elegido = filedialog.askopenfilename(
            parent=self, initialdir=str(self._carpeta), title="Selecciona el documento",
            filetypes=[("Documentos", "*.pdf *.txt *.xls *.xlsx"), ("Todos los archivos", "*.*")])
        if elegido:
            self.ruta = Path(elegido)
            self.destroy()

    def _omitir_todos(self) -> None:
        self.omitir_todos = True
        self.destroy()


class VentanaPreguntas(ctk.CTkToplevel):
    """Pop-up para capturar los datos manuales de la bitácora.

    modo: 'completo' | 'particular' | 'hemo' | 'simple' (ver Interaccion.pedir_datos_manuales).
    """

    _CAMPOS = {
        "operador": ("Operador que trabajó el alta:", "Ej. Karina, Paty, Alberto..."),
        "broker": ("Broker (PDS):", "Ej. GNP, N/A..."),
        "vobo": ("Quién dio VoBo (Supervisor):", "Ej. MARIO, N/A..."),
        "hora_cajas": ("Hora mandada a cajas:", "Ej. 14:30..."),
        "respuesta_hab": ("Quién contestó en habitación:", "Ej. PX 15:00..."),
    }
    _POR_MODO = {
        "completo": ["broker", "vobo", "hora_cajas", "respuesta_hab"],
        "particular": ["vobo", "hora_cajas", "respuesta_hab"],
        "hemo": ["vobo", "hora_cajas"],
        "simple": ["vobo", "hora_cajas"],
    }

    def __init__(self, master, paciente: str, episodio: str, modo: str = "completo",
                 broker_sugerido: str = "",
                 sugeridos: Optional[DatosManuales] = None,
                 pedir_operador: bool = False):
        super().__init__(master)
        self.title("Faltan Datos")
        self.datos: Optional[DatosManuales] = None
        claves_base = list(self._POR_MODO.get(modo, self._POR_MODO["completo"]))
        self._claves = (["operador"] + claves_base) if pedir_operador else claves_base
        self._entradas = {}

        ancho, alto = 400, 300 + 85 * len(self._claves)
        master.update_idletasks()
        x = master.winfo_x() + master.winfo_width() // 2 - ancho // 2
        y = master.winfo_y() + master.winfo_height() // 2 - alto // 2
        self.geometry(f"{ancho}x{alto}+{x}+{y}")
        self.after(100, self._tomar_foco)

        etiqueta_modo = {"hemo": "  (Hemodiálisis: solo se llama a cajas)",
                         "particular": "  (Particular)"}.get(modo, "")
        ctk.CTkLabel(self,
                     text=f"Paciente: {paciente}\nEpisodio: {episodio}{etiqueta_modo}",
                     font=("Roboto", 16, "bold"),
                     text_color="#3b8ed0",
                     justify="center").pack(pady=(20, 10))

        for numero, clave in enumerate(self._claves, start=1):
            titulo, ejemplo = self._CAMPOS[clave]
            ctk.CTkLabel(self, text=f"{numero}. {titulo}",
                         font=("Roboto", 12, "bold")).pack(pady=(10, 0))
            entrada = ctk.CTkEntry(self, placeholder_text=ejemplo, width=300)
            entrada.pack(pady=(0, 5))

            # --- Precarga ---
            valor = ""
            if sugeridos is not None:
                valor = getattr(sugeridos, clave, "") or ""
            if clave == "broker" and broker_sugerido and not valor:
                valor = broker_sugerido
            if valor and valor.strip().upper() != "N/A":
                entrada.insert(0, valor)

            entrada.bind("<Return>", lambda _e, c=clave: self._al_enter(c))
            self._entradas[clave] = entrada

        self.lbl_error = ctk.CTkLabel(self, text="", text_color="red",
                                      font=("Roboto", 12, "bold"))
        self.lbl_error.pack(pady=(0, 5))
        ctk.CTkButton(self, text="Guardar y Continuar (Enter)", font=("Roboto", 14),
                      command=self.guardar_datos).pack(pady=(10, 20))
        self.bind("<Return>", lambda _e: self.guardar_datos())

    def _tomar_foco(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
            # Enfocar automáticamente el primer campo vacío (o el último si todos tienen valor)
            for clave in self._claves:
                entrada = self._entradas[clave]
                if not entrada.get().strip():
                    entrada.focus_set()
                    return
            if self._claves:
                self._entradas[self._claves[-1]].focus_set()
        except TclError:
            log.debug("No se pudo tomar el foco del pop-up (ventana aún no visible).")

    def _al_enter(self, clave_actual: str) -> None:
        """Al presionar Enter en un campo: salta al siguiente vacío o guarda si ya están todos."""
        for clave in self._claves:
            if not self._entradas[clave].get().strip():
                if clave != clave_actual or not self._entradas[clave_actual].get().strip():
                    self._entradas[clave].focus_set()
                    return
        self.guardar_datos()

    def guardar_datos(self) -> None:
        valores = {clave: entrada.get().strip() for clave, entrada in self._entradas.items()}
        if not all(valores.values()):
            self.lbl_error.configure(text="¡Atención: Por favor llena todos los campos!")
            return
        self.datos = DatosManuales(**valores)     # los campos que no se preguntan quedan en 'N/A'
        self.destroy()
