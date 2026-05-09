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

## 6. Resumen del Flujo de Ejecución

1.  **Inicio**: `main.py` carga la configuración del `.env`.
2.  **Extracción**: `STIVScraper` abre el navegador, filtra prospectos y descarga los PDFs.
3.  **Seguimiento**: Se actualiza el `manifest.csv` con el estado de cada archivo.
4.  **Procesamiento**: El `OCRAgent` y el `Parser` transforman los PDFs binarios en archivos de texto estructurado (`.txt`, `.md` o `.json`).

---
**Nota para principiantes**: El código sigue el principio de *Responsabilidad Única*. El scraper solo descarga, el parser solo analiza y el manifest solo registra. Esto hace que sea fácil arreglar una parte sin romper las demás.
