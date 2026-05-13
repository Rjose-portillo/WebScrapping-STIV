# 🎯 Objetivos del Proyecto: Pipeline de Inteligencia Financiera

Este documento define el marco estratégico y los objetivos técnicos del proyecto de web scraping y análisis de prospectos financieros, desarrollado como parte integral de la tesis de **Ingeniería Matemática**.

---

## 🏛️ Objetivo General
Automatizar de manera masiva y resiliente la recolección, normalización y estructuración de información financiera contenida en los prospectos de fondos de inversión en México, con el fin de generar una base de datos histórica que permita el análisis comparativo y estadístico de instrumentos financieros.

---

## 🛠️ Objetivos Específicos

### 1. Extracción Multifuente y Omnicanal
- Desarrollar agentes de scraping capaces de navegar portales complejos y dinámicos (ASP.NET / DevExpress) como el portal **STIV de la CNBV** y el sitio de fondos de **HSBC México**.
- Implementar técnicas de navegación humana (*human-like navigation*) y estrategias de reintento para asegurar la continuidad del pipeline frente a fallas de red o bloqueos de IP.

### 2. Transformación de Datos Unstructured a Structured
- Aplicar procesamiento estructural de documentos para extraer tablas financieras y bloques de texto específicos de PDFs.
- Implementar un motor de **OCR (Reconocimiento Óptico de Caracteres)** para digitalizar prospectos antiguos o escaneados, asegurando que ningún dato quede fuera del análisis.

### 3. Integridad y Gobernanza de Datos (Data Governance)
- Garantizar la unicidad de la información mediante el uso de **hashes SHA-256**, evitando la duplicidad de registros y sesgos en el análisis histórico.
- Implementar un sistema de trazabilidad completa mediante un `manifest.csv` y logs detallados que registren el origen, versión y fecha de consulta de cada documento.

### 4. Persistencia y Modelado para Análisis de Tesis
- Diseñar y poblar una base de datos relacional (**SQLite**) que organice la información en entidades (Instituciones, Fondos, Series, Documentos).
- Almacenar métricas clave de comparación como:
  - Gastos Totales (TER).
  - Valor en Riesgo (VaR).
  - Rendimientos Netos y Benchmarks asociados.
  - Horizontes de inversión y calificaciones crediticias.

### 5. Escalabilidad y Preparación para IA (Data Readiness)
- Asegurar que los datos estén listos para alimentar modelos de procesamiento de lenguaje natural (NLP) o sistemas de generación aumentada por recuperación (RAG).
- Crear herramientas de inspección rápida para auditar la salud de la recolección de datos en tiempo real.

---

## 📈 Impacto Esperado
Para la tesis de Ingeniería Matemática, este proyecto busca:
1.  **Reducción del Sesgo Humano**: Eliminar errores de captura manual de datos financieros.
2.  **Análisis de Gran Angular**: Permitir comparativas masivas entre operadoras de fondos que actualmente son difíciles de realizar de forma manual.
3.  **Base Histórica**: Construir un repositorio de datos que no existe de forma pública y estructurada, permitiendo estudios de series de tiempo sobre costos y riesgos en el mercado mexicano.

---
**Documento generado como guía estratégica para el desarrollo de la Tesis Profesional.**
