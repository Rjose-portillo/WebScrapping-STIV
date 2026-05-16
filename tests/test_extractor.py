"""
Suite de pruebas para el extractor de comisiones y rendimientos CNBV.

Valida:
    - Extraccion de comisiones desde texto corrido.
    - Extraccion de comisiones desde tablas estructuradas.
    - Parsing de la tabla de rendimientos con coordenadas de celdas.
    - Normalizacion de valores porcentuales.
    - Persistencia en base de datos.
    - Diferenciacion Fondo vs Benchmark.

Ejecucion:
    python -m pytest tests/test_extractor.py -v
    python tests/test_extractor.py  # Ejecucion directa
"""

import sys
import os
import json
import tempfile
import unittest

# Agregar directorio raiz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.parser.cnbv_extractor import (
    CNBVExtractor,
    _normalize_percentage,
    _parse_table_cells,
    _find_cell_by_keyword,
    _extract_numeric_value,
)
from src.agents.parser.document_engine import Block
from src.database.db_manager import DatabaseManager


class TestNormalizePercentage(unittest.TestCase):
    """Tests para la funcion de normalizacion de porcentajes."""

    def test_standard_format(self):
        self.assertEqual(_normalize_percentage("1.50%"), "1.50%")

    def test_without_percent_sign(self):
        self.assertEqual(_normalize_percentage("1.50"), "1.50%")

    def test_comma_decimal(self):
        self.assertEqual(_normalize_percentage("0,75%"), "0.75%")

    def test_negative_value(self):
        self.assertEqual(_normalize_percentage("-0.32%"), "-0.32%")

    def test_with_spaces(self):
        self.assertEqual(_normalize_percentage(" 2.10 % "), "2.10%")

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_percentage(None))

    def test_empty_returns_none(self):
        self.assertIsNone(_normalize_percentage(""))

    def test_integer(self):
        self.assertEqual(_normalize_percentage("3%"), "3.00%")

    def test_zero(self):
        self.assertEqual(_normalize_percentage("0.00%"), "0.00%")


class TestExtractNumericValue(unittest.TestCase):
    """Tests para extraccion de valores numericos."""

    def test_simple_percentage(self):
        self.assertEqual(_extract_numeric_value("El TER es 1.50%"), "1.50%")

    def test_negative(self):
        self.assertEqual(_extract_numeric_value("Rendimiento: -0.32%"), "-0.32%")

    def test_no_value(self):
        self.assertIsNone(_extract_numeric_value("Sin datos"))


class TestParseTableCells(unittest.TestCase):
    """Tests para el parser de celdas de tabla."""

    def test_basic_table_parsing(self):
        table_text = """[TABLE_START page=1 index=1]
[ROW 1]
[CELL row=1 col=1]
Concepto
[CELL row=1 col=2]
Valor
[ROW 2]
[CELL row=2 col=1]
Comision por Administracion anual
[CELL row=2 col=2]
1.50%
[ROW 3]
[CELL row=3 col=1]
Gastos Totales (TER)
[CELL row=3 col=2]
2.10%
[TABLE_END]"""
        cells = _parse_table_cells(table_text)

        self.assertEqual(cells[(1, 1)], "Concepto")
        self.assertEqual(cells[(1, 2)], "Valor")
        self.assertEqual(cells[(2, 1)], "Comision por Administracion anual")
        self.assertEqual(cells[(2, 2)], "1.50%")
        self.assertEqual(cells[(3, 1)], "Gastos Totales (TER)")
        self.assertEqual(cells[(3, 2)], "2.10%")

    def test_empty_cells_excluded(self):
        table_text = """[TABLE_START page=1 index=1]
[ROW 1]
[CELL row=1 col=1]
(vacío)
[CELL row=1 col=2]
Dato real
[TABLE_END]"""
        cells = _parse_table_cells(table_text)
        self.assertNotIn((1, 1), cells)
        self.assertEqual(cells[(1, 2)], "Dato real")


class TestFindCellByKeyword(unittest.TestCase):
    """Tests para busqueda de celdas por keyword."""

    def setUp(self):
        self.cells = {
            (1, 1): "Concepto",
            (1, 2): "Porcentaje",
            (2, 1): "Comision por Administracion",
            (2, 2): "1.50%",
            (3, 1): "Gastos Totales (TER)",
            (3, 2): "2.10%",
            (4, 1): "Comision por Desempeno",
            (4, 2): "No aplica",
        }

    def test_find_administracion(self):
        result = _find_cell_by_keyword(self.cells, ["administraci"])
        self.assertEqual(result, "1.50%")

    def test_find_ter(self):
        result = _find_cell_by_keyword(self.cells, ["gastos totales", "ter"])
        self.assertEqual(result, "2.10%")

    def test_find_desempeno(self):
        result = _find_cell_by_keyword(self.cells, ["desempeño", "desempeno"])
        self.assertEqual(result, "No aplica")

    def test_not_found(self):
        result = _find_cell_by_keyword(self.cells, ["inexistente"])
        self.assertIsNone(result)


class TestCNBVExtractorComisiones(unittest.TestCase):
    """Tests para la extraccion de comisiones del CNBVExtractor."""

    def setUp(self):
        self.extractor = CNBVExtractor()

    def test_extract_comisiones_from_table(self):
        """Extrae comisiones desde un bloque de tabla estructurada."""
        table_block = Block(
            kind="table",
            top=100.0,
            bottom=200.0,
            text="""[TABLE_START page=3 index=1]
[ROW 1]
[CELL row=1 col=1]
Estructura de Costos
[CELL row=1 col=2]
Porcentaje
[ROW 2]
[CELL row=2 col=1]
Comision por Administracion Anual
[CELL row=2 col=2]
1.25%
[ROW 3]
[CELL row=3 col=1]
Comision por Desempeno
[CELL row=3 col=2]
0.50%
[ROW 4]
[CELL row=4 col=1]
Gastos Totales (TER)
[CELL row=4 col=2]
1.85%
[TABLE_END]""",
            page=3,
            index=1,
        )
        blocks = [table_block]
        result = self.extractor._extract_comisiones(blocks)

        self.assertEqual(result["comision_administracion_anual"], "1.25%")
        self.assertEqual(result["comision_desempeno"], "0.50%")
        self.assertEqual(result["gastos_totales_ter"], "1.85%")

    def test_extract_comisiones_from_text(self):
        """Extrae comisiones desde texto corrido (seccion)."""
        section_block = Block(
            kind="section",
            top=50.0,
            bottom=150.0,
            text="""[SECTION_START page=2 index=1 x0=50.0 x1=550.0]
La Comision por Administracion anual es de 1.75% sobre el valor del activo neto.
La Comision por desempeno es de 0.00%.
Los Gastos Totales (TER) son de 2.30% anualizado.
[SECTION_END]""",
            page=2,
            index=1,
        )
        blocks = [section_block]
        result = self.extractor._extract_comisiones(blocks)

        self.assertEqual(result["comision_administracion_anual"], "1.75%")
        self.assertEqual(result["comision_desempeno"], "0.00%")
        self.assertEqual(result["gastos_totales_ter"], "2.30%")

    def test_extract_comisiones_no_cobra(self):
        """Detecta 'No cobra' como 0% para comision por desempeno."""
        section_block = Block(
            kind="section",
            top=50.0,
            bottom=150.0,
            text="""[SECTION_START page=2 index=1 x0=50.0 x1=550.0]
Comision por Administracion anual: 1.00%
Comision por Desempeno: No cobra
Gastos Totales (TER): 1.20%
[SECTION_END]""",
            page=2,
            index=1,
        )
        blocks = [section_block]
        result = self.extractor._extract_comisiones(blocks)

        self.assertEqual(result["comision_administracion_anual"], "1.00%")
        self.assertEqual(result["comision_desempeno"], "0.00%")
        self.assertEqual(result["gastos_totales_ter"], "1.20%")


class TestCNBVExtractorRendimientos(unittest.TestCase):
    """Tests para la extraccion de rendimientos del CNBVExtractor."""

    def setUp(self):
        self.extractor = CNBVExtractor()

    def test_extract_rendimientos_from_table(self):
        """Extrae rendimientos diferenciando Fondo vs Benchmark con celdas."""
        table_block = Block(
            kind="table",
            top=300.0,
            bottom=500.0,
            text="""[TABLE_START page=5 index=1]
[ROW 1]
[CELL row=1 col=1]
Rendimientos Netos
[CELL row=1 col=2]
1 Mes
[CELL row=1 col=3]
3 Meses
[CELL row=1 col=4]
12 Meses
[CELL row=1 col=5]
3 Anos
[ROW 2]
[CELL row=2 col=1]
Fondo
[CELL row=2 col=2]
0.85%
[CELL row=2 col=3]
2.50%
[CELL row=2 col=4]
8.75%
[CELL row=2 col=5]
25.30%
[ROW 3]
[CELL row=3 col=1]
Indice de Referencia
[CELL row=3 col=2]
0.92%
[CELL row=3 col=3]
2.70%
[CELL row=3 col=4]
9.10%
[CELL row=3 col=5]
26.50%
[TABLE_END]""",
            page=5,
            index=1,
        )

        # Agregar un bloque con fecha de corte
        section_block = Block(
            kind="section",
            top=280.0,
            bottom=295.0,
            text="""[SECTION_START page=5 index=1 x0=50.0 x1=550.0]
Fecha de corte: 31/03/2024
[SECTION_END]""",
            page=5,
            index=1,
        )

        blocks = [section_block, table_block]
        result = self.extractor._extract_rendimientos(blocks)

        # Verificar periodos del fondo
        self.assertEqual(result["periodos"]["1_mes"], "0.85%")
        self.assertEqual(result["periodos"]["3_meses"], "2.50%")
        self.assertEqual(result["periodos"]["12_meses"], "8.75%")
        self.assertEqual(result["periodos"]["3_anios"], "25.30%")

        # Verificar periodos del benchmark
        self.assertEqual(result["benchmark"]["1_mes"], "0.92%")
        self.assertEqual(result["benchmark"]["3_meses"], "2.70%")
        self.assertEqual(result["benchmark"]["12_meses"], "9.10%")
        self.assertEqual(result["benchmark"]["3_anios"], "26.50%")

        # Verificar fecha de corte
        self.assertEqual(result["fecha_corte"], "31/03/2024")

    def test_extract_rendimientos_negative_values(self):
        """Maneja rendimientos negativos correctamente."""
        table_block = Block(
            kind="table",
            top=300.0,
            bottom=500.0,
            text="""[TABLE_START page=4 index=1]
[ROW 1]
[CELL row=1 col=1]
Desempeno Historico
[CELL row=1 col=2]
1 Mes
[CELL row=1 col=3]
3 Meses
[CELL row=1 col=4]
12 Meses
[ROW 2]
[CELL row=2 col=1]
Serie del Fondo
[CELL row=2 col=2]
-0.45%
[CELL row=2 col=3]
-1.20%
[CELL row=2 col=4]
3.50%
[ROW 3]
[CELL row=3 col=1]
Benchmark
[CELL row=3 col=2]
-0.30%
[CELL row=3 col=3]
-0.80%
[CELL row=3 col=4]
4.20%
[TABLE_END]""",
            page=4,
            index=1,
        )
        blocks = [table_block]
        result = self.extractor._extract_rendimientos(blocks)

        self.assertEqual(result["periodos"]["1_mes"], "-0.45%")
        self.assertEqual(result["periodos"]["3_meses"], "-1.20%")
        self.assertEqual(result["periodos"]["12_meses"], "3.50%")
        self.assertEqual(result["benchmark"]["1_mes"], "-0.30%")
        self.assertEqual(result["benchmark"]["12_meses"], "4.20%")


class TestDatabaseManager(unittest.TestCase):
    """Tests para la persistencia en base de datos."""

    def setUp(self):
        """Crea una base de datos temporal para cada test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_tesis.db")
        self.db = DatabaseManager(db_path=self.db_path)

    def tearDown(self):
        """Limpia la base de datos temporal."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def _sample_data(self):
        """Genera datos de prueba completos."""
        return {
            "metadata": {
                "hash_archivo": "abc123def456",
                "url_stiv": "https://stiv.cnbv.gob.mx",
                "nombre_archivo": "FONDO1_Prospecto_2024.pdf",
            },
            "fondo_serie": {
                "clave_pizarra": "FONDBX1",
                "serie_accionaria": "BF1",
                "categoria": "Renta Variable",
                "tipo_administracion": "Activa",
                "benchmark_oficial": "S&P/BMV IPC",
                "horizonte_inversion": "Largo plazo (3+ anos)",
            },
            "metricas_riesgo": {
                "var_maximo_autorizado": "4.50%",
                "var_promedio_observado": "2.10%",
                "calificacion_crediticia": "AAA",
                "calificacion_riesgo_mercado": "5",
            },
            "estructura_costos": {
                "comision_administracion_anual": "1.25%",
                "comision_desempeno": "0.50%",
                "gastos_totales_ter": "1.85%",
            },
            "rendimientos_historicos": {
                "periodos": {
                    "1_mes": "0.85%",
                    "3_meses": "2.50%",
                    "12_meses": "8.75%",
                    "3_anios": "25.30%",
                },
                "benchmark": {
                    "1_mes": "0.92%",
                    "3_meses": "2.70%",
                    "12_meses": "9.10%",
                    "3_anios": "26.50%",
                },
                "fecha_corte": "31/03/2024",
            },
        }

    def test_save_extraction_complete(self):
        """Guarda una extraccion completa con comisiones y rendimientos."""
        data = self._sample_data()
        result = self.db.save_extraction_result(data, "HSBC")
        self.assertTrue(result)

    def test_duplicate_hash_rejected(self):
        """Rechaza documentos con hash duplicado."""
        data = self._sample_data()
        self.db.save_extraction_result(data, "HSBC")
        # Segundo intento con mismo hash debe fallar
        result = self.db.save_extraction_result(data, "HSBC")
        self.assertFalse(result)

    def test_performance_vs_cost_query(self):
        """Verifica que la consulta analitica funciona."""
        data = self._sample_data()
        self.db.save_extraction_result(data, "HSBC")
        results = self.db.get_performance_vs_cost_data()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["clave_pizarra"], "FONDBX1")
        self.assertEqual(results[0]["gastos_totales_ter"], "1.85%")
        self.assertEqual(results[0]["rendimiento_12m"], "8.75%")

    def test_comisiones_summary(self):
        """Verifica resumen de comisiones."""
        data = self._sample_data()
        self.db.save_extraction_result(data, "CNBV_STIV")
        results = self.db.get_comisiones_summary()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["comision_desempeno"], "0.50%")

    def test_rendimientos_all_periods(self):
        """Verifica que todos los periodos de rendimiento se guardan."""
        data = self._sample_data()
        self.db.save_extraction_result(data, "HSBC")
        results = self.db.get_all_rendimientos()
        self.assertEqual(len(results), 4)  # 4 periodos
        periodos = {r["periodo"] for r in results}
        self.assertEqual(periodos, {"1_mes", "3_meses", "12_meses", "3_anios"})

    def test_database_summary(self):
        """Verifica el resumen general."""
        data = self._sample_data()
        self.db.save_extraction_result(data, "HSBC")
        summary = self.db.get_database_summary()
        self.assertEqual(summary["instituciones"], 1)
        self.assertEqual(summary["fondos"], 1)
        self.assertEqual(summary["documentos"], 1)
        self.assertEqual(summary["rendimientos_historicos"], 4)

    def test_error_data_not_saved(self):
        """No guarda datos con error."""
        data = {"error": "Documento vacio"}
        result = self.db.save_extraction_result(data, "HSBC")
        self.assertFalse(result)

    def test_missing_pizarra_not_saved(self):
        """No guarda datos sin clave de pizarra."""
        data = self._sample_data()
        data["fondo_serie"]["clave_pizarra"] = None
        result = self.db.save_extraction_result(data, "HSBC")
        self.assertFalse(result)


class TestFullExtraction(unittest.TestCase):
    """Test de integracion: extraccion completa desde bloques simulados."""

    def test_full_prospecto_extraction(self):
        """Simula un prospecto completo y verifica toda la cadena."""
        extractor = CNBVExtractor()

        # Crear archivo temporal para hash
        temp_dir = tempfile.mkdtemp()
        temp_file = os.path.join(temp_dir, "test_prospecto.txt")
        with open(temp_file, "w") as f:
            f.write("Contenido simulado de prospecto")

        # Simular bloques como los generaria document_engine
        blocks = [
            Block(kind="section", top=10, bottom=50,
                  text="""[SECTION_START page=1 index=1 x0=50.0 x1=550.0]
Clave de Pizarra: FONDRV
Serie Accionaria: BF5
Categoria: Renta Variable
Tipo de Administracion: Activa
Horizonte de Inversion sugerido: Largo plazo mayor a 3 anos
[SECTION_END]""", page=1, index=1),
            Block(kind="section", top=60, bottom=100,
                  text="""[SECTION_START page=1 index=2 x0=50.0 x1=550.0]
VaR maximo autorizado: 5.00%
VaR promedio observado: 2.80%
Calificacion crediticia: AA
Calificacion de riesgo de mercado: 6
[SECTION_END]""", page=1, index=2),
            Block(kind="table", top=120, bottom=250,
                  text="""[TABLE_START page=2 index=1]
[ROW 1]
[CELL row=1 col=1]
Estructura de Costos
[CELL row=1 col=2]
Porcentaje Anual
[ROW 2]
[CELL row=2 col=1]
Comision por Administracion Anual
[CELL row=2 col=2]
1.80%
[ROW 3]
[CELL row=3 col=1]
Comision por Desempeno
[CELL row=3 col=2]
0.00%
[ROW 4]
[CELL row=4 col=1]
Gastos Totales (TER)
[CELL row=4 col=2]
2.15%
[TABLE_END]""", page=2, index=1),
            Block(kind="section", top=260, bottom=280,
                  text="""[SECTION_START page=3 index=1 x0=50.0 x1=550.0]
Fecha de corte: 30/06/2024
[SECTION_END]""", page=3, index=1),
            Block(kind="table", top=290, bottom=400,
                  text="""[TABLE_START page=3 index=2]
[ROW 1]
[CELL row=1 col=1]
Rendimientos Netos Anualizados
[CELL row=1 col=2]
1 Mes
[CELL row=1 col=3]
3 Meses
[CELL row=1 col=4]
12 Meses
[CELL row=1 col=5]
3 Anos
[ROW 2]
[CELL row=2 col=1]
Rendimiento del Fondo
[CELL row=2 col=2]
1.20%
[CELL row=2 col=3]
3.80%
[CELL row=2 col=4]
12.50%
[CELL row=2 col=5]
35.00%
[ROW 3]
[CELL row=3 col=1]
Indice de Referencia (S&P/BMV IPC)
[CELL row=3 col=2]
1.10%
[CELL row=3 col=3]
3.50%
[CELL row=3 col=4]
11.80%
[CELL row=3 col=5]
33.20%
[TABLE_END]""", page=3, index=2),
        ]

        # Probar extraccion de comisiones
        comisiones = extractor._extract_comisiones(blocks)
        self.assertEqual(comisiones["comision_administracion_anual"], "1.80%")
        self.assertEqual(comisiones["comision_desempeno"], "0.00%")
        self.assertEqual(comisiones["gastos_totales_ter"], "2.15%")

        # Probar extraccion de rendimientos
        rendimientos = extractor._extract_rendimientos(blocks)
        self.assertEqual(rendimientos["periodos"]["1_mes"], "1.20%")
        self.assertEqual(rendimientos["periodos"]["12_meses"], "12.50%")
        self.assertEqual(rendimientos["benchmark"]["12_meses"], "11.80%")
        self.assertEqual(rendimientos["fecha_corte"], "30/06/2024")

        # Limpiar
        os.remove(temp_file)
        os.rmdir(temp_dir)


# ---------------------------------------------------------------------------
# Ejecucion directa con datos de prueba para poblar la DB
# ---------------------------------------------------------------------------
def populate_sample_data():
    """Inserta datos de prueba para el dashboard de tesis."""
    print("=" * 60)
    print("GENERANDO DATOS DE PRUEBA PARA DASHBOARD")
    print("=" * 60)

    db = DatabaseManager(db_path="data/tesis_prospectos.db")

    sample_funds = [
        {
            "metadata": {"hash_archivo": f"hash_sample_{i:03d}",
                         "url_stiv": "https://stiv.cnbv.gob.mx",
                         "nombre_archivo": f"FONDO{i}_Prospecto.pdf"},
            "fondo_serie": {
                "clave_pizarra": pizarra,
                "serie_accionaria": serie,
                "categoria": cat,
                "tipo_administracion": tipo,
                "benchmark_oficial": bench,
                "horizonte_inversion": horiz,
            },
            "metricas_riesgo": {
                "var_maximo_autorizado": var_max,
                "var_promedio_observado": var_prom,
                "calificacion_crediticia": calif,
                "calificacion_riesgo_mercado": riesgo,
            },
            "estructura_costos": {
                "comision_administracion_anual": admin,
                "comision_desempeno": desemp,
                "gastos_totales_ter": ter,
            },
            "rendimientos_historicos": {
                "periodos": {"1_mes": r1, "3_meses": r3,
                             "12_meses": r12, "3_anios": r36},
                "benchmark": {"1_mes": b1, "3_meses": b3,
                              "12_meses": b12, "3_anios": b36},
                "fecha_corte": "31/03/2024",
            },
        }
        for i, (pizarra, serie, cat, tipo, bench, horiz,
                var_max, var_prom, calif, riesgo,
                admin, desemp, ter,
                r1, r3, r12, r36,
                b1, b3, b12, b36) in enumerate([
            ("HSBCBOL", "BF1", "Renta Variable", "Activa", "S&P/BMV IPC",
             "Largo plazo", "6.00%", "3.20%", "AAA", "6",
             "1.80%", "0.50%", "2.45%",
             "1.20%", "3.80%", "12.50%", "35.00%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("HSBCDOL", "BF2", "Deuda", "Pasiva", "CETES 28",
             "Corto plazo", "2.00%", "0.80%", "AAA", "2",
             "0.75%", "0.00%", "0.95%",
             "0.90%", "2.70%", "10.50%", "28.00%",
             "0.88%", "2.65%", "10.20%", "27.50%"),
            ("ACTINVR", "B", "Renta Variable", "Activa", "S&P/BMV IPC",
             "Largo plazo", "7.50%", "4.10%", "AA", "7",
             "2.20%", "1.00%", "3.50%",
             "0.50%", "1.50%", "6.80%", "18.50%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("GBMCRE", "BF", "Renta Variable", "Activa", "S&P/BMV IPC",
             "Largo plazo", "5.50%", "2.90%", "AAA", "5",
             "1.50%", "0.00%", "1.80%",
             "1.50%", "4.20%", "14.80%", "42.00%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("SURGBM", "A", "Renta Variable", "Pasiva", "S&P/BMV IPC",
             "Mediano plazo", "4.00%", "2.50%", "AA+", "4",
             "0.50%", "0.00%", "0.65%",
             "1.05%", "3.40%", "11.50%", "32.80%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("HSBCMEX", "BF3", "Renta Variable", "Activa", "S&P/BMV IPC",
             "Largo plazo", "5.80%", "3.00%", "AAA", "5",
             "1.60%", "0.25%", "2.05%",
             "1.30%", "3.90%", "13.20%", "38.00%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("NAFDEUD", "B", "Deuda", "Activa", "CETES 91",
             "Corto plazo", "1.50%", "0.50%", "AAA", "1",
             "0.90%", "0.00%", "1.10%",
             "0.85%", "2.55%", "9.80%", "26.50%",
             "0.82%", "2.50%", "9.50%", "25.80%"),
            ("SCOTRV", "F", "Renta Variable", "Activa", "MSCI Mexico",
             "Largo plazo", "8.00%", "4.50%", "A+", "7",
             "2.50%", "1.50%", "4.20%",
             "0.30%", "0.90%", "4.50%", "12.00%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("INVEXRV", "BF", "Renta Variable", "Activa", "S&P/BMV IPC",
             "Largo plazo", "6.50%", "3.50%", "AA", "6",
             "1.90%", "0.75%", "2.80%",
             "0.80%", "2.40%", "9.20%", "25.00%",
             "1.10%", "3.50%", "11.80%", "33.20%"),
            ("BXPLUS", "B", "Deuda", "Pasiva", "UDIBONOS",
             "Mediano plazo", "3.00%", "1.20%", "AAA", "3",
             "0.60%", "0.00%", "0.75%",
             "0.95%", "2.85%", "11.00%", "30.50%",
             "0.90%", "2.75%", "10.80%", "29.80%"),
        ], start=1)
    ]

    saved_count = 0
    for fund_data in sample_funds:
        institution = "HSBC" if "HSBC" in fund_data["fondo_serie"]["clave_pizarra"] else "CNBV_STIV"
        if db.save_extraction_result(fund_data, institution):
            saved_count += 1
            print(f"  ✅ Guardado: {fund_data['fondo_serie']['clave_pizarra']}")
        else:
            print(f"  ⚠️  Saltado (duplicado): {fund_data['fondo_serie']['clave_pizarra']}")

    print(f"\n{'='*60}")
    print(f"Total guardados: {saved_count}/{len(sample_funds)}")
    print(f"Base de datos: data/tesis_prospectos.db")
    print(f"{'='*60}")

    # Verificar
    summary = db.get_database_summary()
    print(f"\nResumen DB: {json.dumps(summary, indent=2)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--populate", action="store_true",
                        help="Inserta datos de prueba en la DB")
    parser.add_argument("--test", action="store_true",
                        help="Ejecuta los tests unitarios")
    args = parser.parse_args()

    if args.populate:
        populate_sample_data()
    elif args.test:
        unittest.main(argv=[""], exit=False)
    else:
        # Ejecutar ambos por defecto
        print("\n🧪 Ejecutando tests unitarios...\n")
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(sys.modules[__name__])
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)

        if result.wasSuccessful():
            print("\n\n✅ Todos los tests pasaron. Poblando base de datos...")
            populate_sample_data()
        else:
            print("\n\n❌ Algunos tests fallaron. Revisa los errores arriba.")
            sys.exit(1)
