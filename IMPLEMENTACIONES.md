# Implementaciones: Extraccion de Comisiones y Rendimientos

## Resumen de Cambios

Este documento describe las implementaciones criticas realizadas para completar el pipeline de extraccion de datos financieros, habilitando el analisis comparativo de fondos de inversion para la tesis de Ingenieria Matematica.

---

## 1. Extraccion Robusta de Comisiones (`src/agents/parser/cnbv_extractor.py`)

### Problema Original
El extractor tenia un placeholder basico que solo buscaba comisiones con patrones regex simples (formato `Clave: Valor`). No funcionaba con:
- Tablas de "Estructura de Costos" con celdas separadas.
- Texto con formatos variables ("es de X%", "de X%", "X% anualizado").
- Documentos donde la comision por desempeno dice "No cobra" o "N/A".

### Solucion Implementada

#### Metodo `_extract_comisiones(blocks)` - Estrategia de 3 Fases:

**Fase 1 - Busqueda en Tablas Estructuradas:**
- Detecta tablas con keywords de costos ("estructura de costos", "comisiones", "gastos", "cuotas").
- Parsea las celdas usando coordenadas `[CELL row=X col=Y]` via `_parse_table_cells()`.
- Busca la etiqueta (ej: "Comision por Administracion") y extrae el valor de la columna adyacente.

**Fase 2 - Busqueda en Texto Corrido (Fallback):**
- Multiples patrones regex con variantes:
  - `Comision por Administracion anual: 1.50%`
  - `La Comision por Administracion anual es de 1.50%`
  - `Administracion de 1.50%`
- Detecta "No cobra" / "N/A" y lo normaliza a `0.00%`.

**Fase 3 - Busqueda en Tablas Genericas (Ultimo Recurso):**
- Recorre todas las tablas del documento buscando coincidencias en cualquier celda.

#### Campos Extraidos:
| Campo | Descripcion |
|-------|-------------|
| `comision_administracion_anual` | Comision por administracion y/o distribucion |
| `comision_desempeno` | Comision por desempeno/rendimiento |
| `gastos_totales_ter` | Total Expense Ratio (Gastos Totales) |

#### Normalizacion:
- Todos los valores se normalizan a formato `X.XX%`.
- Acepta formatos con coma decimal, espacios, con/sin signo %.

---

## 2. Parsing de Tabla de Rendimientos (`_extract_rendimientos`)

### Problema Original
El metodo era un placeholder que solo buscaba la fecha de corte dentro de la tabla, sin extraer valores numericos ni diferenciar Fondo de Benchmark.

### Solucion Implementada

#### Algoritmo de Parsing por Coordenadas de Celdas:

**Paso A - Mapeo de Columnas a Periodos:**
```
Busca en las primeras filas (encabezados) celdas que contengan:
  "1 mes" -> col_to_period[col] = "1_mes"
  "3 meses" -> col_to_period[col] = "3_meses"
  "12 meses" / "1 ano" -> col_to_period[col] = "12_meses"
  "3 anos" / "36 meses" -> col_to_period[col] = "3_anios"
```

**Paso B - Identificacion de Filas (Fondo vs Benchmark):**
```
- Marca filas de encabezado (las que contienen texto de periodos).
- Busca keywords en col 1 de cada fila:
  - "fondo", "serie", "rendimiento del fondo" -> fund_row
  - "benchmark", "indice", "referencia" -> benchmark_row
- Fallback heuristico: primera fila con datos numericos puros = fondo,
  segunda = benchmark.
```

**Paso C - Extraccion de Valores:**
```
Para cada columna mapeada a un periodo:
  rendimientos["periodos"][periodo] = valor de cells[(fund_row, col)]
  rendimientos["benchmark"][periodo] = valor de cells[(benchmark_row, col)]
```

#### Estructura de Datos Resultante:
```json
{
  "periodos": {
    "1_mes": "0.85%",
    "3_meses": "2.50%",
    "12_meses": "8.75%",
    "3_anios": "25.30%"
  },
  "benchmark": {
    "1_mes": "0.92%",
    "3_meses": "2.70%",
    "12_meses": "9.10%",
    "3_anios": "26.50%"
  },
  "fecha_corte": "31/03/2024"
}
```

---

## 3. Base de Datos - Esquema Actualizado (`src/database/db_manager.py`)

### Nuevas Columnas en `metricas_prospecto`:
| Columna | Tipo | Descripcion |
|---------|------|-------------|
| `comision_desempeno` | TEXT | Comision por desempeno (nueva) |
| `benchmark_oficial` | TEXT | Indice de referencia oficial |
| `var_promedio_observado` | TEXT | VaR promedio (nuevo) |
| `calificacion_crediticia` | TEXT | Calificacion crediticia |

### Constraint en `rendimientos_historicos`:
- `UNIQUE(documento_id, periodo)` - Evita duplicacion de periodos.

### Sistema de Migraciones:
- `_migrate_db()` agrega columnas nuevas automaticamente a DBs existentes.
- No pierde datos al actualizar el esquema.

### Nuevas Consultas Analiticas:
| Metodo | Retorna |
|--------|---------|
| `get_performance_vs_cost_data()` | JOIN completo para scatter plot (TER vs Rend 12M) |
| `get_all_rendimientos()` | Todos los rendimientos con metadata |
| `get_comisiones_summary()` | Resumen de costos por fondo |
| `get_database_summary()` | Conteos generales |

---

## 4. Dashboard Analitico (`app.py`)

### Stack Tecnologico:
- **Streamlit** para la interfaz web.
- **Plotly Express** para graficas interactivas.
- **Pandas + NumPy** para manipulacion de datos.

### Vistas Implementadas:

#### 4.1 Resumen General
- Metricas KPI (instituciones, fondos, documentos, rendimientos).
- Estado de la base de datos.
- Tabla de muestra de datos disponibles.

#### 4.2 Desempeno vs Costo (Scatter Plot Principal)
- **Eje X**: TER (Gastos Totales) - A menor valor, mas barato.
- **Eje Y**: Rendimiento Neto 12 meses - A mayor valor, mejor.
- **Color**: Categoria del fondo (Renta Variable, Deuda, etc.).
- **Simbolo**: Institucion.
- **Lineas de referencia**: Medianas como divisorias de cuadrantes.
- **Cuadrantes de interpretacion**:
  - Superior-Izquierdo: Fondos EFICIENTES (baratos + alto rendimiento).
  - Inferior-Derecho: Fondos INEFICIENTES (caros + bajo rendimiento).
- **Filtros**: Categoria e Institucion.
- **Estadisticas**: Promedios, ratio rendimiento/costo.

#### 4.3 Analisis de Comisiones
- Histograma de distribucion del TER.
- Bar chart de TER promedio por institucion.
- Box plots de comision admin y comision desempeno por categoria.
- Tabla completa ordenable.

#### 4.4 Rendimientos Historicos
- Barras agrupadas Fondo vs Benchmark por periodo.
- Metrica Alpha (exceso de retorno sobre benchmark).
- Vista agregada del universo completo.
- Selector individual de fondos.

---

## 5. Suite de Pruebas (`tests/test_extractor.py`)

### Cobertura:
- **32 tests unitarios** que cubren:
  - Normalizacion de porcentajes (9 cases).
  - Extraccion numerica (3 cases).
  - Parsing de celdas de tabla (2 cases).
  - Busqueda por keyword en celdas (4 cases).
  - Extraccion de comisiones desde tabla (1 case).
  - Extraccion de comisiones desde texto (2 cases).
  - Extraccion de rendimientos con Fondo vs Benchmark (2 cases).
  - Persistencia en DB (7 cases).
  - Integracion completa (1 case).
  - Edge cases (datos vacios, duplicados, errores).

### Datos de Prueba:
- `--populate`: Inserta 10 fondos de muestra en la DB para visualizacion.
- Los fondos cubren: HSBC, operadoras independientes, renta variable, deuda, activa, pasiva.

---

## 6. Archivos Modificados/Creados

| Archivo | Accion | Descripcion |
|---------|--------|-------------|
| `src/agents/parser/cnbv_extractor.py` | Reescrito | Extractor completo con comisiones y rendimientos |
| `src/database/db_manager.py` | Reescrito | Schema expandido + migraciones + consultas analiticas |
| `app.py` | Creado | Dashboard Streamlit con 4 vistas analiticas |
| `tests/test_extractor.py` | Creado | Suite de 32 tests + generador de datos de prueba |
| `requirements.txt` | Actualizado | Agregado streamlit, plotly, numpy |
| `IMPLEMENTACIONES.md` | Creado | Este documento |

---

## 7. Ejecucion

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar tests
python tests/test_extractor.py --test

# Poblar base de datos con datos de muestra
python tests/test_extractor.py --populate

# Lanzar dashboard
streamlit run app.py

# Pipeline completo de extraccion (requiere PDFs)
python main.py
```

---

## 8. Impacto en la Tesis

Con estas implementaciones, el pipeline ahora permite:

1. **Analisis Cuantitativo de Eficiencia**: El scatter plot TER vs Rendimiento 12M identifica automaticamente fondos eficientes vs ineficientes.

2. **Comparacion Institucional**: Se puede medir si HSBC cobra mas o menos que operadoras independientes por rendimientos equivalentes.

3. **Medicion de Alpha**: Para cada fondo se calcula el exceso de retorno sobre su benchmark, cuantificando el valor agregado por la gestion activa.

4. **Base para Modelos Estadisticos**: Los datos normalizados en la DB estan listos para regresiones, clustering y otros analisis de la tesis.

---

*Documento generado como parte del pipeline de Inteligencia Financiera - Tesis de Ingenieria Matematica.*
