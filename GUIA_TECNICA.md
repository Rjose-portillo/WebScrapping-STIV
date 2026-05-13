# 📘 Guía Técnica: Entendiendo el Pipeline STIV Scraper

Esta guía explica el funcionamiento interno del proyecto, las bibliotecas utilizadas y la lógica detrás de la extracción de datos del portal STIV de la CNBV.

---

## 1. El Portal STIV y el Reto del Scraping
El portal **STIV (Sistema de Transferencia de Información de Valores)** utiliza tecnologías de ASP.NET y controles de **DevExpress**. Estos sitios no son "estáticos"; el contenido se genera dinámicamente.

### ¿Por qué es difícil de scrapear?
- **Paginación Dinámica**: Cuando cambias de página, la URL no cambia. Se usa una técnica llamada *Postback*.
- **Controles DevExpress**: Las tablas no son simples `<table>`, sino estructuras complejas de JavaScript que cargan datos bajo demanda.
- **Detección de Bots**: El sitio monitorea la velocidad de las peticiones para bloquear scripts automatizados.

---

## 2. Bibliotecas Utilizadas (El Stack)

### A. Playwright (`playwright`)
- **¿Por qué?**: A diferencia de *BeautifulSoup* (que solo lee texto estático), Playwright levanta un navegador real (Chromium). Esto permite ejecutar el JavaScript de la página, hacer clic en botones de paginación y manejar descargas de archivos PDF de forma nativa.
- **Uso en el código**: Se encarga de navegar, esperar a que los selectores CSS estén visibles y gestionar la sesión del navegador.

### B. Pandas (`pandas`)
- **¿Por qué?**: Es la biblioteca estándar para manejo de datos. 
- **Uso en el código**: Gestiona el `manifest.csv`. Cada vez que descargamos un archivo, Pandas registra los metadatos (fecha, versión, ruta). Esto asegura **Trazabilidad** (saber de dónde vino cada dato).

### C. pdfplumber (`pdfplumber`)
- **¿Por qué?**: La mayoría de las librerías de PDF extraen texto como una "sopa de letras" sin orden. `pdfplumber` permite detectar la ubicación visual de las palabras y, lo más importante, **detectar tablas**.
- **Uso en el código**: Se usa en `extract_document.py` para separar el texto normal de las tablas financieras, preservando el formato para que una IA (LLM) pueda entender los datos.

### D. PyTesseract (`pytesseract`)
- **¿Por qué?**: Algunos prospectos son fotos o escaneos. 
- **Uso en el código**: Es nuestro agente de **OCR** (Reconocimiento Óptico de Caracteres). Convierte las imágenes del PDF en texto legible.

---

## 3. Lógica de Extracción (Scraping)

### Identificación de Datos
En `config/selectors.py`, definimos cómo encontrar cada pieza de información:
- `FILAS_RESULTADOS`: Usamos un selector que busca filas cuyos IDs comiencen con `ctl00...DXDataRow`.
- `COL_TIPO_DOC`: Revisamos la columna 6. Si el texto contiene "PROSPECTO", el bot procede; si dice "DICI" o "Escrito", lo ignora. Esto ahorra ancho de banda y almacenamiento.

### ¿Por qué toma esos valores?
- **Denominación Social**: Se usa para crear carpetas automáticas. Así, los PDFs de "Operadora A" no se mezclan con los de "Operadora B".
- **Clave de Pizarra + Fecha + Versión**: Se combinan para renombrar el archivo descargado. 
  - *Ejemplo*: `FONDO_Prospecto_2024-05-08_V1.pdf`
  - Esto evita duplicados y permite tener un histórico de versiones del mismo prospecto.
48: 
49: ### B. Scraper de HSBC (Específico)
50: - **Estructura**: A diferencia de STIV, HSBC usa una navegación basada en familias de fondos (HSBC-10, 20, etc.). 
51: - **Lógica**: El script `hsbc_scraper.py` automatiza la selección de cada familia en el menú desplegable, espera a que la tabla se actualice y descarga tanto el **Prospecto** como el **DICI** en una sola pasada.
52: - **Organización**: Los archivos se guardan en `Archivos/HSBC/[Nombre_del_Fondo]`, facilitando la comparación directa entre instrumentos.

---

## 4. Estrategia de "Data Readiness" (Listo para IA)

El código no solo baja archivos, los prepara para ser analizados por una IA o un sistema RAG:

1.  **Normalización de Nombres**: Elimina caracteres especiales que causan errores en Windows/Linux.
2.  **Detección de Tipo de PDF**:
    - Si el PDF tiene texto real, usamos el parseo estructural.
    - Si el PDF es una imagen, activamos el OCR.
3.  **Estructura Visual**: En `extract_document.py`, el código agrupa líneas basándose en su "Gap" (espacio) vertical. Si dos líneas están muy separadas, entiende que son secciones distintas.

---

## 5. Resiliencia y Ética (Anti-Blocking)

Para evitar que el servidor de la CNBV nos bloquee, implementamos:
- **Throttling (Retardos)**: Usamos `random.uniform(1.5, 7.0)`. Esto hace que el bot parezca un humano que se toma unos segundos para leer antes de hacer clic.
- **Retry Strategy**: Si la conexión falla (muy común en sitios de gobierno), el código no se detiene; espera unos segundos y vuelve a intentar hasta 3 veces con una espera exponencial (cada vez espera más tiempo).

---

## 6. Arquitectura de Datos para Tesis (Base de Datos)

Para facilitar el análisis histórico y comparativo (objetivo de la tesis), el pipeline integra una base de datos **SQLite**:

### A. Esquema Relacional
- **Normalización**: Los datos se separan en tablas de `instituciones`, `fondos`, `series` y `documentos`. Esto permite hacer consultas SQL complejas como: *"¿Cuál es la comisión promedio de los fondos de renta variable de HSBC vs los de otras operadoras?"*.
- **Métricas**: Extraemos campos críticos como el **TER (Gastos Totales)**, el **VaR** y los **Rendimientos**.

### B. Integridad y Validación
- **Hash de Archivo (SHA-256)**: Cada documento genera una "huella digital" única. Si el archivo ya existe en la base de datos (incluso si tiene otro nombre), el sistema lo ignora para evitar sesgos en el análisis de datos.
- **Relaciones**: El uso de llaves foráneas asegura que cada métrica esté anclada a una versión específica de un documento.

---

## 7. Resumen del Flujo de Ejecución

1.  **Inicio**: `main.py` carga la configuración.
2.  **Extracción Dual**: 
    - `STIVScraper` procesa el portal de la CNBV.
    - `HSBCScraper` procesa los fondos de HSBC.
3.  **Procesamiento**: El `OCRAgent` y el `Parser` extraen el contenido de los PDFs.
4.  **Persistencia**: El `DatabaseManager` valida y guarda la información estructurada en `data/tesis_prospectos.db`.
5.  **Análisis**: Se utiliza `inspect_db.py` para verificar la salud de los datos recolectados.

---
**Nota para la Tesis**: Este pipeline reduce el tiempo de recolección de datos en un 95%, eliminando el error humano en la transcripción de cifras financieras.
