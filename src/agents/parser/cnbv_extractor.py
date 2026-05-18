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
    Maneja artefactos de PDF como '2.N9/1D%' -> '2.91%' (N/D intercalado)
    Retorna None si no puede interpretar el valor.
    """
    if not raw:
        return None
    cleaned = raw.strip().replace(",", ".").replace(" ", "")
    
    # Handle interleaved N/D artifact (e.g., "2.N9/1D%" -> "2.91%")
    # These occur when pdfplumber merges two overlapping sub-columns
    # Pattern: digits mixed with N, /, D characters from "N/D" text
    has_digits = bool(re.search(r"\d", cleaned))
    has_nd_chars = bool(re.search(r"[NnDd]", cleaned)) and "/" in cleaned
    if has_nd_chars and has_digits:
        # Strip out N, D, / characters to recover the numeric value
        numeric_only = re.sub(r"[NnDd/]", "", cleaned).replace("%", "").strip()
        try:
            val = float(numeric_only)
            return f"{val:.2f}%"
        except (ValueError, TypeError):
            pass
    # Pure N/D, N/A values
    upper_clean = cleaned.upper().replace("%", "").replace(" ", "").replace(".", "")
    if upper_clean in ("N/D", "ND", "N/A", "NA", "NOCOBRA", "NOAPLICA", "NINGUNA"):
        return None
    
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
            "volatilidad_historica": re.compile(
                r"Volatilidad\s+hist[oó]rica\s*:?\s*(-?[\d.,]+\s*%?)",
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

    # -------------------------------------------------------------------
    # Metodos de analisis financiero para tesis
    # -------------------------------------------------------------------
    def _calculate_risk_level(self, var_str: Optional[str], vol_str: Optional[str]) -> Optional[int]:
        """Calcula el nivel de riesgo (1-7) basado en estandares CNBV (CUFI).

        Prioriza el VaR maximo autorizado para la clasificacion.
        """
        def parse_val(s: Optional[str]) -> Optional[float]:
            if not s: return None
            try:
                return float(s.replace("%", "").replace(",", "."))
            except: return None

        val = parse_val(var_str) or parse_val(vol_str)
        if val is None:
            return None

        # Umbrales tipicos de la CNBV para VaR diario (95% confianza)
        if val <= 0.3: return 1   # Muy Bajo
        if val <= 0.6: return 2   # Bajo
        if val <= 1.1: return 3   # Bajo a Moderado
        if val <= 1.6: return 4   # Moderado
        if val <= 2.1: return 5   # Moderado a Alto
        if val <= 4.0: return 6   # Alto
        return 7                  # Muy Alto

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
        """Extrae comisiones usando estrategia dual: texto corrido + celdas de tabla."""
        result = {
            "comision_administracion_anual": None,
            "comision_desempeno": None,
            "gastos_totales_ter": None,
        }

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

        all_text = self._get_full_text(blocks)

        for field_key, patterns_list in self.comision_patterns.items():
            result_key = {
                "comision_admin": "comision_administracion_anual",
                "comision_desempeno": "comision_desempeno",
                "ter": "gastos_totales_ter",
            }[field_key]

            if result[result_key] is not None:
                continue

            for pattern in patterns_list:
                match = pattern.search(all_text)
                if match:
                    raw_value = match.group(1).strip()
                    if re.match(r"(?:No|N/?A|Ninguna)", raw_value, re.IGNORECASE):
                        result[result_key] = "0.00%"
                    else:
                        result[result_key] = _normalize_percentage(raw_value)
                    break

        return result

    # -------------------------------------------------------------------
    # EXTRACCION DE RENDIMIENTOS HISTORICOS
    # -------------------------------------------------------------------
    def _extract_rendimientos(self, blocks: List[Block]) -> Dict[str, Any]:
        """Extrae la tabla de rendimientos netos con diferenciacion Fondo vs Benchmark."""
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

        period_identifiers = {
            "1_mes": re.compile(r"(?:1|un|[úu]ltimo)\s*mes(?!es)", re.IGNORECASE | re.DOTALL),
            "3_meses": re.compile(r"(?:3|tres)\s*meses|[úu]ltimos\s*3", re.IGNORECASE | re.DOTALL),
            "12_meses": re.compile(r"(?:12|doce)\s*meses|(?:1|un)\s*a[nñ]o|[úu]ltimos\s*12", re.IGNORECASE | re.DOTALL),
            "3_anios": re.compile(r"(?:3|tres)\s*a[nñ]os?|(?:36)\s*meses", re.IGNORECASE | re.DOTALL),
        }

        fund_row_keywords = re.compile(
            r"(?:rendimiento\s+neto|rendimiento\s+del\s+fondo|fondo|cartera)", re.IGNORECASE)
        benchmark_row_keywords = re.compile(
            r"(?:benchmark|[ií]ndice|referencia|comparativo|base)", re.IGNORECASE)

        table_blocks = self._get_table_blocks(blocks)
        for tblock in table_blocks:
            content_lower = tblock.text.lower()
            is_rendimiento_table = any(kw in content_lower for kw in [
                "rendimientos netos", "rendimiento neto",
                "rendimiento y desempeño", "rendimiento y desempeno",
                "tabla de rendimientos",
                "desempeño histórico", "desempeno historico",
                "rendimientos históricos", "rendimientos historicos",
                "desempeño del fondo", "desempeno del fondo",
                "rendimientos anualizados", "rendimientos efectivos",
                "performance",
            ])

            if not is_rendimiento_table:
                continue

            cells = _parse_table_cells(tblock.text)
            if not cells:
                continue

            max_row = max(r for r, c in cells.keys()) if cells else 0
            max_col = max(c for r, c in cells.keys()) if cells else 0

            col_to_period: Dict[int, str] = {}
            for row_idx in range(1, min(8, max_row + 1)):
                for col_idx in range(1, max_col + 1):
                    cell_text = cells.get((row_idx, col_idx), "")
                    for period_key, period_re in period_identifiers.items():
                        if period_re.search(cell_text):
                            col_to_period[col_idx] = period_key
                            break

            fund_row, benchmark_row = None, None
            header_row_set = set()
            for row_idx in range(1, max_row + 1):
                for col_idx in range(1, max_col + 1):
                    cell_text = cells.get((row_idx, col_idx), "")
                    for period_re in period_identifiers.values():
                        if period_re.search(cell_text):
                            header_row_set.add(row_idx)
                            break

            for row_idx in range(1, max_row + 1):
                if row_idx in header_row_set: continue
                for col_idx in range(1, min(3, max_col + 1)):
                    cell_text = cells.get((row_idx, col_idx), "")
                    if fund_row_keywords.search(cell_text) and fund_row is None:
                        fund_row = row_idx
                    elif benchmark_row_keywords.search(cell_text) and benchmark_row is None:
                        benchmark_row = row_idx

            if col_to_period:
                for col_idx, period_key in col_to_period.items():
                    if fund_row is not None:
                        val = cells.get((fund_row, col_idx), "")
                        normalized = _normalize_percentage(val)
                        if normalized: rendimientos["periodos"][period_key] = normalized
                    if benchmark_row is not None:
                        val = cells.get((benchmark_row, col_idx), "")
                        normalized = _normalize_percentage(val)
                        if normalized: rendimientos["benchmark"][period_key] = normalized

            fecha = self._extract_from_text(tblock.text, self.patterns["fecha_corte"])
            if fecha: rendimientos["fecha_corte"] = fecha
            if any(v is not None for v in rendimientos["periodos"].values()): break

        if not any(v is not None for v in rendimientos["periodos"].values()):
            all_text = self._get_full_text(blocks)
            # Strategy 2: Text-based rendimiento table parsing (fitz output)
            rendimientos = self._extract_rendimientos_from_text(all_text, rendimientos)

        if not any(v is not None for v in rendimientos["periodos"].values()):
            # Strategy 3: Simple regex patterns
            all_text = self._get_full_text(blocks)
            for period_key, patterns_list in self.rendimiento_patterns.items():
                for pattern in patterns_list:
                    match = pattern.search(all_text)
                    if match:
                        rendimientos["periodos"][period_key] = _normalize_percentage(match.group(1))
                        break

        if not rendimientos["fecha_corte"]:
            rendimientos["fecha_corte"] = self._find_in_blocks(blocks, "fecha_corte")

        return rendimientos

    def _extract_rendimientos_from_text(self, text: str, rendimientos: Dict[str, Any]) -> Dict[str, Any]:
        """Extrae rendimientos de texto plano (formato DICI CNBV/HSBC).
        
        Busca patrones como:
            Rendimiento neto
            N/D
            2.85%
            N/D
            2.01%
            ...
        
        Donde los valores corresponden a: 1 Mes, 3 Meses, 12 Meses, luego años.
        """
        lines = text.split("\n")
        
        # Find "Rendimiento neto" line
        rend_neto_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\s*rendimiento\s+neto\s*$", line.strip(), re.IGNORECASE):
                rend_neto_idx = i
                break
        
        if rend_neto_idx is None:
            return rendimientos
        
        # Also try to find header pattern to determine column positions
        # Look backwards for "Último Mes", "Últimos 3 Meses", "Últimos 12 Meses"
        header_found = False
        period_columns = []  # List of period keys in order
        
        for i in range(max(0, rend_neto_idx - 15), rend_neto_idx):
            line_lower = lines[i].strip().lower()
            if "ltimo" in line_lower and "mes" in line_lower and "12" not in line_lower and "3" not in line_lower:
                period_columns.append("1_mes")
                header_found = True
            elif ("3" in line_lower and "mes" in line_lower) or "ltimos 3" in line_lower:
                period_columns.append("3_meses")
            elif ("12" in line_lower and "mes" in line_lower) or "ltimos 12" in line_lower:
                period_columns.append("12_meses")
            elif re.match(r"^\s*\d{4}\s*$", lines[i].strip()):
                # Year column - could be annual return
                pass
        
        if not period_columns:
            # Default assumption for DICI format
            period_columns = ["1_mes", "3_meses", "12_meses"]
        
        # Extract values after "Rendimiento neto"
        values = []
        for i in range(rend_neto_idx + 1, min(rend_neto_idx + 25, len(lines))):
            line = lines[i].strip()
            if not line:
                continue
            # Stop if we hit another label
            if re.match(r"^[A-Za-záéíóúÁÉÍÓÚñÑ\s]+$", line) and len(line) > 5:
                # Check if it's a label like "Tasa libre de riesgo"
                if any(kw in line.lower() for kw in ["tasa", "riesgo", "serie", "válida", "pérdida", "índice"]):
                    break
            # Try to extract percentage value
            match = re.match(r"^\s*(-?\d+[.,]?\d*)\s*%?\s*$", line)
            if match:
                values.append(_normalize_percentage(match.group(0)))
            elif line.upper() in ("N/D", "N/A", "-", "ND"):
                values.append(None)  # Placeholder for missing
        
        # Map values to periods
        # In HSBC format, values alternate: val_for_period1, val_for_period2, ...
        # But sometimes there are N/D entries interspersed
        # Filter out None (N/D) entries to get actual values
        actual_values = [v for v in values if v is not None]
        
        # Map to periods based on detected column headers
        period_keys = ["1_mes", "3_meses", "12_meses", "3_anios"]
        
        if len(actual_values) >= 1 and len(period_columns) > 0:
            for idx, period_key in enumerate(period_columns):
                if idx < len(actual_values) and period_key in period_keys:
                    if rendimientos["periodos"].get(period_key) is None:
                        rendimientos["periodos"][period_key] = actual_values[idx]
        elif len(actual_values) >= 3:
            # Fallback: assign first 3 values to 1M, 3M, 12M
            mapping = list(zip(period_keys[:len(actual_values)], actual_values))
            for period_key, val in mapping:
                if rendimientos["periodos"].get(period_key) is None:
                    rendimientos["periodos"][period_key] = val
        
        # Try to find benchmark (Índice) values
        indice_idx = None
        for i in range(rend_neto_idx + 1, min(rend_neto_idx + 60, len(lines))):
            line_lower = lines[i].strip().lower()
            if re.match(r"^\s*[ií]ndice\s*$", line_lower) or "rendimiento del índice" in line_lower:
                indice_idx = i
                break
        
        if indice_idx is not None:
            bench_values = []
            for i in range(indice_idx + 1, min(indice_idx + 20, len(lines))):
                line = lines[i].strip()
                if not line:
                    continue
                if re.match(r"^[A-Za-záéíóúÁÉÍÓÚñÑ\s]+$", line) and len(line) > 5:
                    if any(kw in line.lower() for kw in ["capital", "serie", "válida", "fecha"]):
                        break
                match = re.match(r"^\s*(-?\d+[.,]?\d*)\s*%?\s*$", line)
                if match:
                    bench_values.append(_normalize_percentage(match.group(0)))
                elif line.upper() in ("N/D", "N/A", "-", "ND"):
                    bench_values.append(None)
            
            actual_bench = [v for v in bench_values if v is not None]
            if len(actual_bench) >= 1:
                for idx, period_key in enumerate(period_columns[:len(actual_bench)]):
                    if period_key in period_keys:
                        if rendimientos["benchmark"].get(period_key) is None:
                            rendimientos["benchmark"][period_key] = actual_bench[idx]
        
        return rendimientos

    # -------------------------------------------------------------------
    # EXTRACCION PRINCIPAL
    # -------------------------------------------------------------------
    def extract(self, file_path: str, url_stiv: str = "Desconocido") -> Dict[str, Any]:
        """Procesa el PDF y retorna el JSON estructurado segun los requerimientos de tesis."""
        path = Path(file_path)
        blocks = extract_document(path)
        if not blocks: return {"error": "Documento vacio"}

        comisiones = self._extract_comisiones(blocks)
        rendimientos = self._extract_rendimientos(blocks)
        var_max = _normalize_percentage(self._find_in_blocks(blocks, "var_max"))
        vol_hist = _normalize_percentage(self._find_in_blocks(blocks, "volatilidad_historica"))
        calif_riesgo = self._find_in_blocks(blocks, "calificacion_riesgo")

        # Determinar tipo de documento (Prospecto o DICI)
        filename_upper = path.name.upper()
        tipo_documento = "DICI" if "DICI" in filename_upper or "DOCUMENTO CLAVE" in filename_upper else "Prospecto"

        # Inferir clave_pizarra y serie de manera robusta
        pizarra_val = self._find_in_blocks(blocks, "clave_pizarra")
        serie_val = self._find_in_blocks(blocks, "serie")

        if not pizarra_val:
            parts = path.name.split('_')
            if len(parts) > 1 and len(parts[0]) <= 15:
                pizarra_val = parts[0]
            else:
                first_word = re.split(r'[\s_\-\+]+', path.name)[0]
                pizarra_val = first_word if len(first_word) <= 12 else "DESCONOCIDO"

        if pizarra_val and " " in pizarra_val:
            p_parts = pizarra_val.split(" ")
            pizarra_val = p_parts[0]
            if not serie_val:
                serie_val = p_parts[1]

        if not serie_val:
            serie_val = "Unica"

        data = {
            "metadata": {
                "hash_archivo": self._calculate_file_hash(path),
                "url_stiv": url_stiv,
                "nombre_archivo": path.name,
                "tipo_documento": tipo_documento,
            },
            "fondo_serie": {
                "clave_pizarra": pizarra_val,
                "serie_accionaria": serie_val,
                "categoria": self._find_in_blocks(blocks, "categoria"),
                "tipo_administracion": self._find_in_blocks(blocks, "tipo_admin"),
                "benchmark_oficial": self._find_in_blocks(blocks, "benchmark_oficial"),
                "horizonte_inversion": self._find_in_blocks(blocks, "horizonte"),
            },
            "metricas_riesgo": {
                "var_maximo_autorizado": var_max,
                "var_promedio_observado": _normalize_percentage(self._find_in_blocks(blocks, "var_prom")),
                "volatilidad_historica": vol_hist,
                "calificacion_crediticia": self._find_in_blocks(blocks, "calificacion_crediticia"),
                "calificacion_riesgo_mercado": calif_riesgo,
                "nivel_riesgo_calculado": self._calculate_risk_level(var_max, vol_hist)
            },
            "estructura_costos": comisiones,
            "rendimientos_historicos": rendimientos,
        }
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
