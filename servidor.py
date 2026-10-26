"""Arranque del servidor local del portal (reemplaza al .cmd manual).

El .cmd original hacía `taskkill /F /IM python.exe`, que mata TODOS los procesos
python.exe, incluido este robot. Aquí solo se cierran los procesos cuya línea de
comandos corresponde al `manage.py runserver` configurado.
"""
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from configuracion import DIR_BASE, Config
from excepciones import ErrorServidor

log = logging.getLogger("SRV")
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ServidorLocal:
    def __init__(self, config: Config):
        self.cfg = config
        self._proceso: Optional[subprocess.Popen] = None
        #self._archivo_log = None
        self._ruta_log = DIR_BASE / "logs" / "servidor.log"

    # ---------------- utilidades ----------------
    def _destino(self) -> Tuple[str, int]:
        url = urlparse(self.cfg.url_login)
        return url.hostname or "127.0.0.1", url.port or 80

    def _puerto_abierto(self) -> bool:
        host, puerto = self._destino()
        try:
            with socket.create_connection((host, puerto), timeout=1):
                return True
        except OSError:
            return False

    def _esperar(self, condicion, timeout: float) -> bool:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.25)
        return condicion()

    def _cola_del_log(self, lineas: int = 15) -> str:
        try:
            texto = self._ruta_log.read_text(encoding="utf-8", errors="replace")
            return "\n".join(texto.splitlines()[-lineas:])
        except OSError:
            return "(sin log)"

    # ---------------- ciclo de vida ----------------
    def iniciar(self) -> None:
        if not self.cfg.servidor_habilitado:
            log.info("Arranque del servidor deshabilitado en la configuración.")
            return

        host, puerto = self._destino()
        if self.cfg.servidor_reiniciar:
            self._cerrar_servidores_previos()
            self._esperar(lambda: not self._puerto_abierto(), 10)

        if self._puerto_abierto():
            log.warning("Ya hay un servidor respondiendo en %s:%s. Se reutiliza (no se arranca otro).",
                        host, puerto)
            return

        manage = Path(self.cfg.servidor_manage)
        if not manage.exists():
            raise ErrorServidor(f"No existe manage.py en '{manage}'. Revisa 'servidor_manage' en config.json.")
        python = shutil.which(self.cfg.servidor_python)
        if not python:
            raise ErrorServidor(f"No se encontró el ejecutable '{self.cfg.servidor_python}' en el PATH.")

        comando = [python, str(manage), "runserver", *self.cfg.servidor_argumentos]
        log.info("Arrancando servidor: %s", " ".join(comando))

        entorno = dict(os.environ, PYTHONUNBUFFERED="1")
        for variable in ("PYTHONHOME", "PYTHONPATH"):   # evita contaminar con el Python 3 del robot
            entorno.pop(variable, None)

        try:
            self._ruta_log.parent.mkdir(exist_ok=True)
            #self._archivo_log = open(self._ruta_log, "ab")
            self._proceso = subprocess.Popen(
                comando, cwd=str(manage.parent), env=entorno,
                #stdout=self._archivo_log, stderr=subprocess.STDOUT,
                creationflags=_SIN_VENTANA)
        except OSError as exc:
            self.detener()
            raise ErrorServidor(f"No se pudo lanzar el servidor: {exc}") from exc

        log.info("Esperando a que el servidor responda en %s:%s (máx %ss)...",
                 host, puerto, self.cfg.timeout_servidor)
        listo = self._esperar(
            lambda: self._proceso.poll() is not None or self._puerto_abierto(),
            self.cfg.timeout_servidor)

        if self._proceso.poll() is not None:
            codigo = self._proceso.returncode
            cola = self._cola_del_log()
            self.detener()
            raise ErrorServidor(f"El servidor se cerró al arrancar (código {codigo}). Últimas líneas del log:\n{cola}")
        if not listo:
            self.detener()
            raise ErrorServidor(f"El servidor no respondió en {self.cfg.timeout_servidor}s "
                                f"(ver {self._ruta_log.name}).")
        log.info("Servidor listo (PID %s).", self._proceso.pid)

    def detener(self) -> None:
        """Cierra SOLO el servidor que arrancó este robot (si así se configuró)."""
        proceso, self._proceso = self._proceso, None
        if proceso is not None and (self.cfg.servidor_detener_al_terminar or proceso.poll() is not None):
            if proceso.poll() is None:
                log.info("Deteniendo servidor (PID %s)...", proceso.pid)
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proceso.pid), "/T", "/F"],
                                       capture_output=True, creationflags=_SIN_VENTANA, timeout=15)
                    else:
                        proceso.terminate()
                    proceso.wait(timeout=10)
                except Exception as exc:
                    log.warning("No se pudo detener limpiamente el servidor: %s", exc)
        elif proceso is not None:
            log.info("El servidor (PID %s) se deja corriendo, según la configuración.", proceso.pid)
        #if self._archivo_log is not None:
         #   try:
          #      self._archivo_log.close()
           # finally:
            #    self._archivo_log = None

    # ---------------- cierre de instancias previas ----------------
    def _cerrar_servidores_previos(self) -> None:
        if os.name != "nt":
            return
        manage = self.cfg.servidor_manage.replace("'", "''")
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{manage}*' -and $_.CommandLine -like '*runserver*' "
            f"-and $_.ProcessId -ne {os.getpid()} }} | "
            "ForEach-Object { $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }")
        try:
            resultado = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30, creationflags=_SIN_VENTANA)
            pids = resultado.stdout.split()
            if pids:
                log.info("Servidor previo cerrado (PID: %s).", ", ".join(pids))
            else:
                log.info("No había un servidor previo corriendo.")
            if resultado.returncode != 0:
                log.warning("PowerShell reportó un problema: %s", resultado.stderr.strip()[:300])
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("No se pudo buscar/cerrar un servidor previo (%s). Se continúa.", exc)
