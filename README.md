# STIV Scraper: Data Pipeline for Financial Prospectuses

Este proyecto es una solución modular de ingeniería de datos diseñada para automatizar la extracción masiva de prospectos de inversión del portal **STIV de la CNBV**. Está construido bajo principios de **Data Governance** y **Data Readiness**, asegurando que los datos extraídos sean consistentes, trazables y estén listos para pipelines de IA (RAG / FinBERT).

---

## 🚀 Funcionalidades Principales

- **Extracción Masiva**: Navega automáticamente por todas las páginas del portal STIV sin necesidad de filtros manuales.
- **Filtro Inteligente**: El agente valida cada registro en memoria y solo descarga aquellos marcados como **"Prospecto"**, ignorando DICIs, escritos y otros documentos no relevantes.
- **Clasificación por Entidad**: Organiza automáticamente las descargas en subcarpetas basadas en la **Denominación Social de la Operadora**.
- **Anti-Blocking System**: Implementa retardos aleatorios (*throttling*) entre descargas y cambios de página para imitar el comportamiento humano y evitar baneos de IP.
- **Data Governance**: Genera un archivo `manifest.csv` que sirve como índice maestro de todas las descargas, incluyendo metadatos como fecha de consulta, versión y ruta de almacenamiento local.
- **Resiliencia**: Incluye una estrategia de reintentos (`RetryStrategy`) para manejar caídas o intermitencias del servidor de la CNBV.

---

## 🛠️ Arquitectura Técnica

El proyecto sigue una estructura modular para facilitar su mantenimiento y escalabilidad:

```text
/
├── config/             # Configuración de entorno y selectores CSS/XPath.
├── src/                # Código fuente del pipeline.
│   ├── agents/
│   │   ├── scraper/    # Agente de navegación y descarga (Playwright).
│   │   └── parser/     # Placeholder para análisis de PDFs y RAG.
├── Archivos/           # Repositorio local de datos (Organizado por Entidad).
├── logs/               # Trazabilidad técnica de la ejecución.
├── main.py             # Orquestador principal del pipeline.
└── init_env.ps1        # Script de automatización de entorno para Windows.
```

### Tecnologías Utilizadas
- **Python 3.10+**
- **Playwright**: Para la navegación y manejo de eventos dinámicos en ASP.NET.
- **Pandas**: Para la gestión del manifiesto de datos y metadatos.
- **Python-dotenv**: Gestión segura de configuraciones.

---

## 🚦 Instalación y Uso

### 1. Requisitos Previos
- Tener Python instalado.
- Clonar este repositorio.

### 2. Configuración del Entorno
Ejecuta el script de inicialización en PowerShell (Windows):
```powershell
.\init_env.ps1
```
*Este comando creará el entorno virtual, instalará las dependencias y configurará los navegadores necesarios.*

### 3. Ejecución
Activa el entorno virtual:
```powershell
.\venv\Scripts\Activate.ps1
```
Lanza el pipeline de extracción:
```powershell
python main.py
```

---

## 📊 Estrategia de Data Readiness

Para que los datos sean útiles en modelos de lenguaje (LLMs) o sistemas RAG, el scraper realiza las siguientes acciones:
1. **Normalización de Nombres**: Los archivos se renombran siguiendo el patrón `PIZARRA_TIPO_FECHA_VERSION.pdf`.
2. **Sanitización**: Se eliminan caracteres especiales de los nombres de carpetas y archivos para compatibilidad con sistemas de archivos Windows/Linux.
3. **Anclaje de Metadatos**: Cada descarga queda registrada con su contexto completo en el `manifest.csv`, permitiendo una trazabilidad total desde el origen hasta el procesamiento final.

---

## 🔒 Seguridad y Buenas Prácticas

- **.gitignore**: El proyecto está configurado para no subir archivos binarios (PDFs), registros de logs, ni variables de entorno sensibles (`.env`) al repositorio.
- **Throttling**: El agente espera entre 1.5s y 7s de forma aleatoria para proteger la infraestructura del portal de origen.

---
**Desarrollado como parte de un pipeline avanzado de Ingeniería de Datos Financieros.**
