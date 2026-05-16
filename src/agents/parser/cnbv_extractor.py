"""
Extractor de Alta Precision para Prospectos de Inversion CNBV (Anexo 2 CUFI).

Transforma bloques de texto y tablas estructurados por document_engine en un
esquema JSON relacional listo para persistencia en SQLite y analisis de tesis.

Capacidades implementadas:
    - Extraccion robusta de comisiones (admin, desempeno, TER) desde texto y tablas.
    - Parsing estructural de la tabla de Rendimientos Netos usando coordenadas de celdas.
    - Diferenciacion precisa entre valores del Fondo y del Benchmark.
    - Busqueda multi-patron con fallback progresivo.
"""

import json
import logging
import re
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from src.agents.parser.document_engine import extract_document, Block

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilidades de normalizacion numerica
# ---------------------------------------------------------------------------
def _normalize_percentage(raw: Optional[str]) -> Optional[str]:
    """Normaliza un valor porcentual a formato consistente 'X.XX%'.

    Acepta formatos: '1.50%', '1.50 %', '1.50', '0,75%', '-0.32%'
    Retorna None si no puede interpretar el valor.
    """
    if not raw:
        return None
    cleaned = raw.strip().replace(",", ".").replace(" ", "")
    # Eliminar el signo % si existe, lo agregaremos despues
    has_percent = "%" in cleaned
    cleaned = cleaned.replace("%", "")
    try:
        val = float(cleaned)
        return f"{val:.2f}%"
    except (ValueError, TypeError):
        return raw.strip() if raw.strip() else None


def _extract_numeric_value(text: str) -> Optional[str]:
    """Extrae el primer valor numerico (posiblemente con signo y %) de un texto."""
    match = re.search(r"(-?\d+[.,]?\d*)\s*%?", text)
    if match:
        raw = match.group(0)
        return _normalize_percentage(raw)
    return None


# ---------------------------------------------------------------------------
# Parsing de celdas estructuradas [CELL row=X col=Y]
# ---------------------------------------------------------------------------
def _parse_table_cells(table_text: str) -> Dict[Tuple[int, int], str]:
    """Parsea el texto serializado de una tabla y retorna un dict {(row, col): contenido}.

    El formato esperado es el generado por document_engine:
        [ROW 1]
        [CELL row=1 col=1]
        Contenido de la celda
        [CELL row=1 col=2]
        ...
    """
    cells: Dict[Tuple[int, int], str] = {}
    # Patron para capturar cada celda con su contenido
    cell_pattern = re.compile(
        r"\[CELL\s+row=(\d+)\s+col=(\d+)\]\s*\n(.*?)(?=\[CELL|\[ROW|\[TABLE_END]|\Z)",
        re.DOTALL
    )
    for match in cell_pattern.finditer(table_text):
        row = int(match.group(1))
        col = int(match.group(2))
        content = match.group(3).strip()
        if content and content != "(vacio)" and content != "(vacío)":
            cells[(row, col)] = content
    return cells


def _find_cell_by_keyword(cells: Dict[Tuple[int, int], str],
                          keywords: List[str],
                          value_col_offset: int = 1) -> Optional[str]:
    """Busca una celda cuyo contenido contenga alguna keyword y retorna
    el valor de la celda en la columna adyacente (offset configurable).
    """
    for (row, col), content in cells.items():
        content_lower = content.lower()
        for kw in keywords:
            if kw.lower() in content_lower:
                # Buscar el valor en la columna con offset
                value_key = (row, col + value_col_offset)
                if value_key in cells:
                    return cells[value_key]
                # Fallback: intentar extraer valor numerico del mismo texto
                # (caso donde etiqueta y valor estan en la misma celda)
                numeric = _extract_numeric_value(content)
                if numeric:
                    return numeric
    return None


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------
class CNBVExtractor:
    """
    Extractor de alta precision para prospectos de inversion de la CNBV (Anexo 2 CUFI).
    Transforma bloques de texto y tablas en un esquema JSON relacional.
    """

    def __init__(self):
        # Patrones para busqueda en texto corrido (secciones)
        self.patterns = {
            "clave_pizarra": re.compile(
                r"Clave\s+de\s+Pizarra\s*:?\s*([A-Z0-9_\-]+)", re.IGNORECASE),
            "serie": re.compile(
                r"Serie\s+(?:Accionaria)?\s*:?\s*([A-Z0-9_\-]+)", re.IGNORECASE),
            "categoria": re.compile(
                r"Categor[ií]a\s*:?\s*(Renta\s+Variable|Deuda|Renta\s+Fija|Cobertura|Mixto)",
                re.IGNORECASE),
            "tipo_admin": re.compile(
                r"Tipo\s+de\s+Administraci[oó]n\s*:?\s*(Activa|Pasiva|Indizada)",
                re.IGNORECASE),
            "horizonte": re.compile(
                r"Horizonte\s+de\s+Inversi[oó]n\s+(?:sugerido)?\s*:?\s*([^\.\n]+)",
                re.IGNORECASE),
            "var_max": re.compile(
                r"VaR\s+m[aá]ximo\s+(?:autorizado)?\s*:?\s*(-?[\d.,]+\s*%?)",
                re.IGNORECASE),
            "var_prom": re.compile(
                r"VaR\s+promedio\s+(?:observado)?\s*:?\s*(-?[\d.,]+\s*%?)",
                re.IGNORECASE),
            "calificacion_crediticia": re.compile(
                r"Calificaci[oó]n\s+crediticia\s*:?\s*([A-Za-z\d]+(?:[\/\-][A-Za-z\d]+)?)",
                re.IGNORECASE),
            "calificacion_riesgo": re.compile(
                r"Calificaci[oó]n\s+de\s+riesgo\s+de\s+mercado\s*:?\s*(\d)",
                re.IGNORECASE),
            "fecha_corte": re.compile(
                r"(?:Fecha\s+de\s+corte|Al|Cifras\s+al|Informaci[oó]n\s+al)\s*:?\s*"
                r"(\d{1,2}[\s/\-](?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|"
                r"julio|agosto|septiembre|octubre|noviembre|diciembre|\d{1,2})"
                r"[\s/\-](?:de\s+)?\d{4}|\d{2}[/\-]\d{2}[/\-]\d{4})",
                re.IGNORECASE),
            "benchmark_oficial": re.compile(
                r"(?:[IÍií]ndice\s+de\s+referencia|[Bb]enchmark|[Rr]eferencia\s+de\s+mercado)"
                r"\s*:?\s*([^\n\.\(\)]{3,80})",
                re.IGNORECASE),
        }

        # Patrones especificos para comisiones (multi-variante)
        # Nota: incluye variantes con "es de", "de", separadores flexibles
        self.comision_patterns = {
            "comision_admin": [
                re.compile(
                    r"[Cc]omisi[oó]n\s+(?:por\s+)?[Aa]dministraci[oó]n\s*"
                    r"(?:anual|annual)?\s*(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)",
                    re.IGNORECASE),
                re.compile(
                    r"[Cc]omisi[oó]n\s+(?:por\s+)?[Aa]dministraci[oó]n\s*"
                    r"(?:y\s+distribuci[oó]n)?\s*(?:anual)?\s*(?:es\s+de|de|:|-|=)?\s*"
                    r"(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(
                    r"[Aa]dministraci[oó]n\s+(?:anual)?\s*(?:es\s+de|de|[-:=])\s*"
                    r"(-?[\d.,]+\s*%?)", re.IGNORECASE),
                # Formato tabla: celda dice "Administracion" y valor esta al lado
                re.compile(
                    r"(?:Cuota\s+de\s+)?[Aa]dministraci[oó]n\s*[-:=]?\s*(-?[\d.,]+\s*%?)",
                    re.IGNORECASE),
            ],
            "comision_desempeno": [
                re.compile(
                    r"[Cc]omisi[oó]n\s+(?:por\s+)?[Dd]esempe[nñ]o\s*"
                    r"(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(
                    r"[Dd]esempe[nñ]o\s*(?:es\s+de|de|[-:=])\s*(-?[\d.,]+\s*%?)",
                    re.IGNORECASE),
                re.compile(
                    r"[Cc]omisi[oó]n\s+(?:por\s+)?[Rr]endimiento\s*"
                    r"(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                # Buscar "No cobra" o "N/A"
                re.compile(
                    r"[Cc]omisi[oó]n\s+(?:por\s+)?[Dd]esempe[nñ]o\s*"
                    r"(?:es\s+de|de|:|-|=)?\s*(No\s+(?:cobra|aplica)|N/?A|Ninguna|0\.?0*%?)",
                    re.IGNORECASE),
            ],
            "ter": [
                re.compile(
                    r"[Gg]astos\s+[Tt]otales\s*\(?(?:TER)?\)?\s*"
                    r"(?:son\s+de|es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)",
                    re.IGNORECASE),
                re.compile(
                    r"TER\s*(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(
                    r"[Rr]atio\s+de\s+[Gg]astos?\s+[Tt]otales?\s*"
                    r"(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(
                    r"[Gg]astos\s+(?:anuales)?\s*(?:del\s+)?[Ff]ondo\s*"
                    r"(?:son\s+de|es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)",
                    re.IGNORECASE),
                re.compile(
                    r"[Rr]az[oó]n\s+de\s+[Gg]astos?\s+[Tt]otales?\s*"
                    r"(?:es\s+de|de|:|-|=)?\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
            ],
        }

        # Patrones para rendimientos en texto
        self.rendimiento_patterns = {
            "1_mes": [
                re.compile(r"(?:1|un)\s*mes\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(r"[Úú]ltimo\s+mes\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
            ],
            "3_meses": [
                re.compile(r"(?:3|tres)\s*meses\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
            ],
            "12_meses": [
                re.compile(r"(?:12|doce)\s*meses\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(r"(?:1|un)\s*a[nñ]o\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
            ],
            "3_anios": [
                re.compile(r"(?:3|tres)\s*a[nñ]os?\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
                re.compile(r"(?:36)\s*meses\s*[-:]\s*(-?[\d.,]+\s*%?)", re.IGNORECASE),
            ],
        }

    # -------------------------------------------------------------------
    # Utilidades internas
    # -------------------------------------------------------------------
    def _calculate_file_hash(self, file_path: Path) -> str:
        """Genera un hash SHA-256 para trazabilidad."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _extract_from_text(self, text: str, pattern: re.Pattern) -> Optional[str]:
        """Extrae la primera coincidencia de un patron regex en texto."""
        match = pattern.search(text)
        return match.group(1).strip() if match else None

    def _find_in_blocks(self, blocks: List[Block], key: str) -> Optional[str]:
        """Busca un campo por clave en todos los bloques (secciones y tablas)."""
        pattern = self.patterns.get(key)
        if not pattern:
            return None
        for block in blocks:
            found = self._extract_from_text(block.text, pattern)
            if found:
                return found
        return None

    def _get_full_text(self, blocks: List[Block]) -> str:
        """Concatena todo el texto de los bloques para busqueda global."""
        return "\n".join(block.text for block in blocks)

    def _get_table_blocks(self, blocks: List[Block]) -> List[Block]:
        """Filtra solo los bloques de tipo tabla."""
        return [b for b in blocks if b.kind == "table"]

    def _get_section_blocks(self, blocks: List[Block]) -> List[Block]:
        """Filtra solo los bloques de tipo seccion."""
        return [b for b in blocks if b.kind == "section"]

    # -------------------------------------------------------------------
    # EXTRACCION DE COMISIONES (Estructura de Costos)
    # -------------------------------------------------------------------
    def _extract_comisiones(self, blocks: List[Block]) -> Dict[str, Optional[str]]:
        """Extrae comisiones usando estrategia dual: texto corrido + celdas de tabla.

        Estrategia:
            1. Buscar en tablas de "Estructura de Costos" / "Comisiones" usando
               coordenadas de celdas para precision.
            2. Fallback a busqueda por regex en texto de secciones.
            3. Normalizacion final de valores porcentuales.
        """
        result = {
            "comision_administracion_anual": None,
            "comision_desempeno": None,
            "gastos_totales_ter": None,
        }

        # --- Fase 1: Busqueda en tablas estructuradas ---
        table_blocks = self._get_table_blocks(blocks)
        cost_table_keywords = [
            "estructura de costos", "comisiones", "gastos", "cuotas",
            "cobros", "cargos al fondo", "costos del fondo"
        ]

        for tblock in table_blocks:
            content_lower = tblock.text.lower()
            is_cost_table = any(kw in content_lower for kw in cost_table_keywords)

            if is_cost_table or "comisi" in content_lower or "ter" in content_lower:
                cells = _parse_table_cells(tblock.text)

                if not result["comision_administracion_anual"]:
                    val = _find_cell_by_keyword(
                        cells,
                        ["administraci", "administración", "admin"],
                        value_col_offset=1
                    )
                    if val:
                        result["comision_administracion_anual"] = _normalize_percentage(val)

                if not result["comision_desempeno"]:
                    val = _find_cell_by_keyword(
                        cells,
                        ["desempeño", "desempeno", "rendimiento", "performance"],
                        value_col_offset=1
                    )
                    if val:
                        result["comision_desempeno"] = _normalize_percentage(val)

                if not result["gastos_totales_ter"]:
                    val = _find_cell_by_keyword(
                        cells,
                        ["gastos totales", "ter", "razón de gastos", "razon de gastos",
                         "total expense", "gastos anuales"],
                        value_col_offset=1
                    )
                    if val:
                        result["gastos_totales_ter"] = _normalize_percentage(val)

        # --- Fase 2: Busqueda en texto corrido (secciones) como fallback ---
        all_text = self._get_full_text(blocks)

        for field_key, patterns_list in self.comision_patterns.items():
            result_key = {
                "comision_admin": "comision_administracion_anual",
                "comision_desempeno": "comision_desempeno",
                "ter": "gastos_totales_ter",
            }[field_key]

            if result[result_key] is not None:
                continue  # Ya encontrado en tabla

            for pattern in patterns_list:
                match = pattern.search(all_text)
                if match:
                    raw_value = match.group(1).strip()
                    # Detectar "No cobra" / "N/A"
                    if re.match(r"(?:No|N/?A|Ninguna)", raw_value, re.IGNORECASE):
                        result[result_key] = "0.00%"
                    else:
                        result[result_key] = _normalize_percentage(raw_value)
                    break

        # --- Fase 3: Busqueda refinada en tablas genéricas (ultimo recurso) ---
        # Algunos documentos no tienen tabla especifica de costos; las comisiones
        # aparecen como filas sueltas en cualquier tabla.
        for field_key, patterns_list in self.comision_patterns.items():
            result_key = {
                "comision_admin": "comision_administracion_anual",
                "comision_desempeno": "comision_desempeno",
                "ter": "gastos_totales_ter",
            }[field_key]

            if result[result_key] is not None:
                continue

            for tblock in table_blocks:
                cells = _parse_table_cells(tblock.text)
                for (row, col), content in cells.items():
                    for pattern in patterns_list:
                        m = pattern.search(content)
                        if m:
                            result[result_key] = _normalize_percentage(m.group(1))
                            break
                    if result[result_key]:
                        break
                if result[result_key]:
                    break

        logger.debug(f"Comisiones extraidas: {result}")
        return result

    # -------------------------------------------------------------------
    # EXTRACCION DE RENDIMIENTOS HISTORICOS
    # -------------------------------------------------------------------
    def _extract_rendimientos(self, blocks: List[Block]) -> Dict[str, Any]:
        """Extrae la tabla de rendimientos netos con diferenciacion Fondo vs Benchmark.

        Estrategia de parsing por coordenadas de celdas:
            1. Localizar la tabla que contiene "rendimientos netos" o "desempeno historico".
            2. Parsear las celdas con _parse_table_cells.
            3. Identificar la fila de encabezados (periodos: 1 mes, 3 meses, etc.).
            4. Identificar la fila del Fondo y la fila del Benchmark.
            5. Mapear los valores a los periodos correspondientes.

        Fallback: busqueda por regex en texto corrido si no se encuentra tabla.
        """
        rendimientos = {
            "periodos": {
                "1_mes": None,
                "3_meses": None,
                "12_meses": None,
                "3_anios": None,
            },
            "benchmark": {
                "1_mes": None,
                "3_meses": None,
                "12_meses": None,
                "3_anios": None,
            },
            "fecha_corte": None,
        }

        # Patrones para detectar encabezados de periodos
        period_identifiers = {
            "1_mes": re.compile(r"(?:1|un)\s*mes", re.IGNORECASE),
            "3_meses": re.compile(r"(?:3|tres)\s*meses", re.IGNORECASE),
            "12_meses": re.compile(r"(?:12|doce)\s*meses|(?:1|un)\s*a[nñ]o", re.IGNORECASE),
            "3_anios": re.compile(r"(?:3|tres)\s*a[nñ]os?|(?:36)\s*meses", re.IGNORECASE),
        }

        # Patrones para identificar filas de fondo vs benchmark
        fund_row_keywords = re.compile(
            r"(?:fondo|serie|rendimiento\s+(?:del\s+)?fondo|neto|cartera)", re.IGNORECASE)
        benchmark_row_keywords = re.compile(
            r"(?:benchmark|[ií]ndice|referencia|comparativo|base)", re.IGNORECASE)

        # --- Fase 1: Buscar en tablas estructuradas ---
        table_blocks = self._get_table_blocks(blocks)
        rendimiento_table_found = False

        for tblock in table_blocks:
            content_lower = tblock.text.lower()
            is_rendimiento_table = any(kw in content_lower for kw in [
                "rendimientos netos", "rendimiento neto",
                "desempeño histórico", "desempeno historico",
                "rendimientos históricos", "rendimientos historicos",
                "desempeño del fondo", "desempeno del fondo",
                "rendimientos anualizados", "performance",
            ])

            if not is_rendimiento_table:
                continue

            rendimiento_table_found = True
            cells = _parse_table_cells(tblock.text)

            if not cells:
                continue

            # Encontrar las filas maximas y columnas maximas
            max_row = max(r for r, c in cells.keys()) if cells else 0
            max_col = max(c for r, c in cells.keys()) if cells else 0

            # --- Paso A: Mapear columnas a periodos ---
            # Buscar en las primeras filas (tipicamente fila 1 o 2 son encabezados)
            col_to_period: Dict[int, str] = {}
            header_rows = range(1, min(4, max_row + 1))  # Revisar primeras 3 filas

            for row_idx in header_rows:
                for col_idx in range(1, max_col + 1):
                    cell_text = cells.get((row_idx, col_idx), "")
                    for period_key, period_re in period_identifiers.items():
                        if period_re.search(cell_text):
                            col_to_period[col_idx] = period_key
                            break

            # --- Paso B: Identificar filas del Fondo y del Benchmark ---
            fund_row = None
            benchmark_row = None

            # Determinar cuales filas son encabezados (contienen texto de periodos)
            header_row_set = set()
            for row_idx in range(1, max_row + 1):
                for col_idx in range(1, max_col + 1):
                    cell_text = cells.get((row_idx, col_idx), "")
                    for period_re in period_identifiers.values():
                        if period_re.search(cell_text):
                            header_row_set.add(row_idx)
                            break

            for row_idx in range(1, max_row + 1):
                if row_idx in header_row_set:
                    continue  # Saltar filas de encabezado
                # Revisar la primera celda de cada fila (tipicamente la etiqueta)
                for col_idx in range(1, min(3, max_col + 1)):
                    cell_text = cells.get((row_idx, col_idx), "")
                    if fund_row_keywords.search(cell_text) and fund_row is None:
                        fund_row = row_idx
                    elif benchmark_row_keywords.search(cell_text) and benchmark_row is None:
                        benchmark_row = row_idx

            # Heuristica: si no se encontraron etiquetas explicitas,
            # asumir que la primera fila de datos es el fondo y la segunda el benchmark
            if col_to_period and fund_row is None:
                # Buscar filas con datos numericos puros (excluyendo encabezados)
                numeric_rows = []
                for r in range(1, max_row + 1):
                    if r in header_row_set:
                        continue  # No contar encabezados como filas numericas
                    has_numeric = False
                    for c in col_to_period.keys():
                        val = cells.get((r, c), "")
                        # Verificar que sea un valor numerico real (no "1 Mes")
                        if re.match(r"^\s*-?[\d.,]+\s*%?\s*$", val):
                            has_numeric = True
                            break
                    if has_numeric:
                        numeric_rows.append(r)

                if len(numeric_rows) >= 2:
                    fund_row = numeric_rows[0]
                    benchmark_row = numeric_rows[1]
                elif len(numeric_rows) == 1:
                    fund_row = numeric_rows[0]

            # --- Paso C: Extraer valores ---
            if col_to_period:
                for col_idx, period_key in col_to_period.items():
                    if fund_row is not None:
                        val = cells.get((fund_row, col_idx), "")
                        normalized = _normalize_percentage(val)
                        if normalized:
                            rendimientos["periodos"][period_key] = normalized

                    if benchmark_row is not None:
                        val = cells.get((benchmark_row, col_idx), "")
                        normalized = _normalize_percentage(val)
                        if normalized:
                            rendimientos["benchmark"][period_key] = normalized

            # Buscar fecha de corte dentro o cerca de la tabla
            fecha = self._extract_from_text(tblock.text, self.patterns["fecha_corte"])
            if fecha:
                rendimientos["fecha_corte"] = fecha

            # Si encontramos tabla con datos, dejamos de buscar
            if any(v is not None for v in rendimientos["periodos"].values()):
                break

        # --- Fase 2: Fallback - Busqueda por regex en texto corrido ---
        if not any(v is not None for v in rendimientos["periodos"].values()):
            logger.debug("No se encontro tabla de rendimientos. Intentando extraccion por regex...")
            all_text = self._get_full_text(blocks)

            for period_key, patterns_list in self.rendimiento_patterns.items():
                for pattern in patterns_list:
                    match = pattern.search(all_text)
                    if match:
                        rendimientos["periodos"][period_key] = _normalize_percentage(
                            match.group(1))
                        break

        # --- Fase 3: Busqueda de fecha de corte global ---
        if not rendimientos["fecha_corte"]:
            rendimientos["fecha_corte"] = self._find_in_blocks(blocks, "fecha_corte")

        logger.debug(f"Rendimientos extraidos: {rendimientos}")
        return rendimientos

    # -------------------------------------------------------------------
    # EXTRACCION PRINCIPAL
    # -------------------------------------------------------------------
    def extract(self, file_path: str, url_stiv: str = "Desconocido") -> Dict[str, Any]:
        """Procesa el PDF y retorna el JSON estructurado segun los requerimientos de tesis.

        Args:
            file_path: Ruta al archivo PDF, DOCX o TXT.
            url_stiv: URL de origen del portal STIV para trazabilidad.

        Returns:
            Diccionario con la estructura:
                metadata, fondo_serie, metricas_riesgo, estructura_costos,
                rendimientos_historicos
        """
        path = Path(file_path)
        logger.info(f"Iniciando extraccion estructurada de: {path.name}")

        # 1. Obtener bloques estructurales
        try:
            blocks = extract_document(path)
        except Exception as e:
            logger.error(f"Error al procesar estructura del documento {file_path}: {e}")
            return {"error": str(e)}

        if not blocks:
            logger.warning(f"No se obtuvieron bloques del documento: {path.name}")
            return {"error": "Documento vacio o sin contenido extraible"}

        # 2. Extraer comisiones (nueva logica robusta)
        comisiones = self._extract_comisiones(blocks)

        # 3. Extraer rendimientos (nueva logica con coordenadas de celdas)
        rendimientos = self._extract_rendimientos(blocks)

        # 4. Mapeo a Entidades
        data = {
            "metadata": {
                "hash_archivo": self._calculate_file_hash(path),
                "url_stiv": url_stiv,
                "nombre_archivo": path.name,
            },
            "fondo_serie": {
                "clave_pizarra": self._find_in_blocks(blocks, "clave_pizarra"),
                "serie_accionaria": self._find_in_blocks(blocks, "serie"),
                "categoria": self._find_in_blocks(blocks, "categoria"),
                "tipo_administracion": self._find_in_blocks(blocks, "tipo_admin"),
                "benchmark_oficial": self._find_in_blocks(blocks, "benchmark_oficial"),
                "horizonte_inversion": self._find_in_blocks(blocks, "horizonte"),
            },
            "metricas_riesgo": {
                "var_maximo_autorizado": _normalize_percentage(
                    self._find_in_blocks(blocks, "var_max")),
                "var_promedio_observado": _normalize_percentage(
                    self._find_in_blocks(blocks, "var_prom")),
                "calificacion_crediticia": self._find_in_blocks(
                    blocks, "calificacion_crediticia"),
                "calificacion_riesgo_mercado": self._find_in_blocks(
                    blocks, "calificacion_riesgo"),
            },
            "estructura_costos": comisiones,
            "rendimientos_historicos": rendimientos,
        }

        # Log de resultados
        ter = comisiones.get("gastos_totales_ter", "N/D")
        r12 = rendimientos["periodos"].get("12_meses", "N/D")
        logger.info(
            f"Extraccion completada para {path.name}. "
            f"TER={ter}, Rend12M={r12}, "
            f"Fecha Corte={rendimientos.get('fecha_corte', 'N/D')}"
        )

        return data

    def save_json(self, data: Dict[str, Any], output_path: str):
        """Guarda el resultado de extraccion como JSON."""
        from pathlib import Path as P
        P(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info(f"Resultado guardado en: {output_path}")


if __name__ == "__main__":
    # Prueba rapida desde linea de comandos
    import sys
    logging.basicConfig(level=logging.DEBUG)
    if len(sys.argv) > 1:
        extractor = CNBVExtractor()
        res = extractor.extract(sys.argv[1])
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print("Uso: python cnbv_extractor.py <ruta_al_pdf>")
