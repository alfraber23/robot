# Registro de Cambios y Áreas de Oportunidad — Robot Digitalizador

## 1. Cambios Implementados (Puntos 1 y 2)

### Punto 1: Corrección de Bugs Críticos (`NameError`) y Lógica en Módulo de Correo
- **Importación de `ErrorCorreo` en [`correo.py`](correo.py) y [`robot.py`](robot.py)**:
  - Se agregó `from excepciones import ErrorCorreo` en `correo.py` y `robot.py`. Antes, si Outlook o `pywin32` fallaban, se lanzaba un `NameError: name 'ErrorCorreo' is not defined` que detenía todo el robot en vez de continuar en modo sin correo.
- **Corrección de conteo booleano en [`ProveedorCorreoOutlook.contar_altas_por_carpeta`](correo.py)**:
  - `_es_correo_altas()` devuelve una tupla `(es_alta, remitente)`. Antes se evaluaba `if self._es_correo_altas(...):` (que en Python siempre es `True` por ser una tupla de 2 elementos). Ahora se desempaca `es_alta, _ = self._es_correo_altas(msg)` y se valida que la fecha esté dentro de `[desde, hasta]`.
- **Implementación de [`ProveedorCorreoOutlook._filtro_fecha`](correo.py)**:
  - Antes devolvía `""`. Ahora genera la consulta Jet de Outlook (`[SentOn]` o `[ReceivedTime]`) para acotar el rango de fechas con `Restrict` (y con respaldo seguro si una carpeta no soporta `Restrict`).
- **Ampliación de cargos en firma y correlación de correos ([`correo.py`](correo.py), [`robot.py`](robot.py), [`modelos.py`](modelos.py))**:
  - Se añadieron las variantes `"Supervisora de Análisis de Cuentas"` y `"Supervisor Análisis de Cuentas"` a `_CARGOS_FIRMA`.
  - Se guarda el correo correlacionado en `Tarea.correo_alta` durante `_filtrar_por_correo` para evitar recalcular la correlación en la fase de bitácora.
  - `_correlacionar` ahora busca el número de episodio no solo en el asunto (`c.asunto`), sino también en los nombres de adjuntos (`c.adjuntos`) y en el cuerpo (`c.cuerpo_texto`).

---

### Punto 2: Operación Web Desatendida y Mejoras de UX
- **Separación de la Fase Web y la Captura Manual en Lote ([`robot.py`](robot.py))**:
  - **Fase 1 (100% automatizada en navegador)**: El robot recorre todos los episodios subiendo los documentos al portal y consultando la hora de alta en ECH de corrido, sin detenerse en cada paciente por la ventana de preguntas manuales.
  - **Fase 2 (Captura manual continua)**: Al terminar todas las subidas web, se muestran en serie las ventanas de captura manual (`VentanaPreguntas`) de los episodios procesados, permitiendo capturar todos los datos de bitácora de una sola vez sin esperas entre pacientes.
- **Barra de progreso determinista ([`interaccion.py`](interaccion.py), [`ui.py`](ui.py), [`robot.py`](robot.py))**:
  - Se agregó `progreso(actual, total)` a `Interaccion` e `InteraccionUI` para mostrar el avance real (`actual / total`) tanto en la fase web como en la captura de bitácora.
- **Atajos de teclado (`Enter`) y foco automático ([`ui.py`](ui.py))**:
  - En `VentanaPreguntas`, al abrirse la ventana el cursor se posiciona automáticamente en el primer campo vacío.
  - Presionar `Enter` salta al siguiente campo vacío o guarda y continúa automáticamente cuando todos los campos están llenos.
  - En `VentanaOpciones`, presionar `Enter` confirma la opción seleccionada.
- **Limpieza de código duplicado/inalcanzable**:
  - Se eliminaron los bloques de código muerto tras `return` en `robot.py` (`_filtrar_por_correo`) y `ui.py` (`pedir_datos_manuales`).

---

### Nuevo Flujo de Búsqueda y Paginación en ECH ([`portal.py`](portal.py))
- **Ingreso directo a `"Altas clínicas (últimos 10 días)"` y configuración de filtro**:
  1. Clic en el engrane (`i.pi.pi-cog`).
  2. Clic en la lista desplegable por ID (`#admissionTypeItem`).
  3. Clic en la casilla `"Marcar todos"` (`div.p-checkbox-box[role="checkbox"][aria-checked="false"]`).
  4. Debajo de `"Incluir en la búsqueda los dados de alta"`, clic en el botón `"Sí"` (`div[role="button"][aria-label="Sí"]`).
  5. Clic en el botón `"Buscar"` (`button#search-worklist-save-worklist`).
- **Lectura de tabla con paginación y caché**:
  - Lee todas las filas de la página actual guardando en caché `(episodio -> hora)` para consultas instantáneas de los demás pacientes.
  - Si el episodio no está en la página actual, avanza de página dando clic en el paginador (`svg.p-paginator-icon` con `path[d^='M5.25 11.1728']` / `button.p-paginator-next`).
  - Se detiene y marca `"NO_ENCONTRADA"` cuando el botón de siguiente página está deshabilitado o cuando las filas de la tabla se repiten respecto a la página anterior.

---

### Selección Automática de Buzón en Cada PC y Respaldo de Operador al Final ([`correo.py`](correo.py), [`configuracion.py`](configuracion.py), [`robot.py`](robot.py), [`ui.py`](ui.py))
- **Autodetección del correo del analista en cada PC**:
  - `ProveedorCorreoOutlook.obtener_carpetas_busqueda()` detecta automáticamente cuál de los 4 `correos_analistas` (`operadorcuentashac1`, `operadorcuentashac2`, `supervisorcuentashac`, `angeles.juarez`) está configurado en el Outlook de la PC actual y busca siempre en **Enviados (`sent`)** y **Recibidos (`inbox`)**.
- **Continuidad si falla Outlook o no hay correos del día**:
  - Si Outlook no puede abrirse, no hay correos del día, o ningún episodio coincide con los correos, deja un **aviso en consola (`WARNING`)** y continúa procesando todos los episodios sin detener el robot.
  - En el formulario del final (`VentanaPreguntas`), cuando un episodio no tiene operador identificado por correo, se muestra automáticamente el campo **"Operador que trabajó el alta:"** para capturarlo manualmente.

---

### Sincronización Forzada entre Nuevo Outlook y Outlook Clásico ([`correo.py`](correo.py), [`configuracion.py`](configuracion.py))
- **`ProveedorCorreoOutlook.forzar_sincronizacion()`**:
  - Al conectar a Outlook Clásico por MAPI/COM, dispara automáticamente `SendAndReceive(False)` e inicia todos los grupos de envío/recepción (`SyncObjects.Item(i).Start()`), esperando `pausa_sincronizacion_correo` (4.0 s por defecto) para que descargue desde Exchange los correos recién enviados o recibidos desde el Nuevo Outlook o la web.
  - Detecta y advierte en consola si Outlook Clásico tiene activado el modo *"Trabajar sin conexión"* (`Offline`).

---

### Refactorización de Búsqueda Cerrada y Precisa en Correo ([`correo.py`](correo.py), [`robot.py`](robot.py), [`configuracion.py`](configuracion.py))
- **Rango estricto de máximo 1 día antes (`[fecha - 1 día, fecha]`)**:
  - Se acotó la búsqueda a ayer y el día actual (`dias_busqueda_correo = 1`), eliminando la revisión de días adicionales o futuros.
- **Filtro `Restrict` con detección de extremo más reciente y tope de 150 correos (`MAX_CORREOS_POR_CARPETA = 150`)**:
  - Aplica primero `carpeta.Items.Restrict(filtro)` para pedir a Exchange solo los correos del rango.
  - Verifica automáticamente cuál extremo de la colección (`Item(1)` vs `Item(total)`) tiene los correos más recientes (evitando leer correos viejos si Outlook invierte el orden).
  - Limita la revisión a máximo **150 correos** por carpeta y corta al encontrar 20 correos seguidos anteriores a `desde`.

---

### Búsqueda y Asignación Automática de Brokers / PDS en Lote ([`configuracion.py`](configuracion.py), [`correo.py`](correo.py), [`robot.py`](robot.py))
- **Catálogo de Brokers (`catalogo_brokers`)**:
  - `VIMED`: `analisis@cvimed.com`
  - `HS`: `cartas.gdl@healthservice.com.mx`, `contacto.gdl@healthservice.com.mx` (se registra como `HS` en bitácora)
  - `AMES`: `ale.amesoluciones@gmail.com`
  - `AXIS`: `axis.reportesdelcarmen@outlook.com`
  - `ARSAM`: `reportepxarsamhealth@outlook.com`
  - `PROMEDIC`: `vinculacion@pmconcierge.com.mx`
- **Detección de reenvíos del Lic. Julio**:
  - Si el correo fue reenviado por Julio (o es un `RV:`/`FW:`/`FWD:`), inspecciona el cuerpo del mensaje para identificar la dirección o dominio original del broker.
- **Ejecución en lote al terminar la fase web (`_identificar_brokers_en_lote`)**:
  - Al finalizar todas las subidas web en Chrome y antes de abrir los formularios manuales, consulta en una sola pasada los correos de brokers de los últimos 7 días (`dias_busqueda_brokers = 7`) y cruza por nombre del paciente o episodio, prellenando automáticamente el campo `Broker (PDS)` en `VentanaPreguntas`.
- **Script de prueba independiente ([`debug_brokers.py`](debug_brokers.py))**:
  - Permite probar y ver en consola los correos de brokers encontrados en la última semana y su cruce con los pacientes del día ejecutando: `py debug_brokers.py`.

---

## 2. Áreas de Oportunidad Pendientes (Priorizadas de Mayor a Menor)

| Prioridad | Área | Descripción | Archivos |
| :--- | :--- | :--- | :--- |
| **3. Alta** | **Seguridad de Credenciales** | Mover usuarios/contraseñas en texto plano a variables de entorno o `config.json` local fuera de la carpeta pública de red. | `configuracion.py` |
| **4. Media-Alta** | **Filtro `Restrict` en `buscar_altas`** | Aplicar `_filtro_fecha` también en `buscar_altas` para reducir iteraciones COM en buzones grandes. | `correo.py` |
| **5. Media** | **Lectura en lote de tabla ECH** | Extraer todas las filas visibles de la tabla de ECH en un diccionario `{episodio: hora}` de una sola vez para ahorrar cambios de pestaña y timeouts de 5s. | `portal.py` |
| **6. Media** | **Índice en memoria en `Bitacora` y log de `ServidorLocal`** | Guardar un `set` de episodios al abrir el Excel en vez de recorrer fila por fila en cada búsqueda, y descomentar el log de `servidor.py`. | `bitacora.py`, `servidor.py` |
| **7. Baja-Media** | **Unificación de parsers de montos y Tests Unitarios** | Unificar la extracción de `"Total a Pagar:"` entre `auditoria.py` y `documentos.py`, y agregar suite de pruebas unitarias. | `auditoria.py`, `documentos.py` |
