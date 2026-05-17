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
            "1_mes": re.compile(r"(?:1|un)\s*mes", re.IGNORECASE),
            "3_meses": re.compile(r"(?:3|tres)\s*meses", re.IGNORECASE),
            "12_meses": re.compile(r"(?:12|doce)\s*meses|(?:1|un)\s*a[nñ]o", re.IGNORECASE),
            "3_anios": re.compile(r"(?:3|tres)\s*a[nñ]os?|(?:36)\s*meses", re.IGNORECASE),
        }

        fund_row_keywords = re.compile(
            r"(?:fondo|serie|rendimiento\s+(?:del\s+)?fondo|neto|cartera)", re.IGNORECASE)
        benchmark_row_keywords = re.compile(
            r"(?:benchmark|[ií]ndice|referencia|comparativo|base)", re.IGNORECASE)

        table_blocks = self._get_table_blocks(blocks)
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

            cells = _parse_table_cells(tblock.text)
            if not cells:
                continue

            max_row = max(r for r, c in cells.keys()) if cells else 0
            max_col = max(c for r, c in cells.keys()) if cells else 0

            col_to_period: Dict[int, str] = {}
            for row_idx in range(1, min(4, max_row + 1)):
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
            for period_key, patterns_list in self.rendimiento_patterns.items():
                for pattern in patterns_list:
                    match = pattern.search(all_text)
                    if match:
                        rendimientos["periodos"][period_key] = _normalize_percentage(match.group(1))
                        break

        if not rendimientos["fecha_corte"]:
            rendimientos["fecha_corte"] = self._find_in_blocks(blocks, "fecha_corte")

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
