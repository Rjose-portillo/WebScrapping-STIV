"""Extrae texto de PDFs y documentos Word preservando la separación visual por secciones y tablas.

El script:
- Detecta tablas con pdfplumber (PDF) o python-docx (Word).
- Excluye sus áreas del texto corrido.
- Divide el contenido restante en líneas y secciones según posición visual.
- Extrae cada celda de tabla por su bounding box (PDF) o estructura nativa (Word).
- Genera una salida etiquetada por página, sección y tabla.
- Soporta extensiones: .pdf, .docx
- Permite procesar múltiples archivos en lote y elegir el formato de salida (txt | json | md).

Uso:
    python extract_document.py archivo.pdf
    python extract_document.py archivo.docx -o salida.txt
    python extract_document.py carpeta/ --batch --format md
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------
# Dependencias opcionales — se importan de forma defensiva para que cada formato
# se cargue solo si el usuario lo necesita.
# ---------------------------------------------------------------------------
try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore

try:
    import docx  # python-docx
    from docx.document import Document as _DocxDocument
    from docx.table import Table as _DocxTable, _Cell as _DocxCell
    from docx.text.paragraph import Paragraph as _DocxParagraph
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
except ImportError:  # pragma: no cover
    docx = None  # type: ignore

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuración global
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

LINE_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "intersection_tolerance": 5,
}

TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
    "intersection_tolerance": 5,
}

logger = logging.getLogger("extract_document")


# ---------------------------------------------------------------------------
# Estructuras de datos
# ---------------------------------------------------------------------------
@dataclass
class Block:
    """Bloque de salida ya serializado.

    Attributes:
        kind: Tipo de bloque, por ejemplo `section` o `table`.
        top: Coordenada superior en la página (o índice secuencial en Word).
        bottom: Coordenada inferior en la página (o índice secuencial en Word).
        text: Contenido final a escribir en la salida.
        left: Coordenada izquierda usada para ordenar bloques.
        page: Número de página al que pertenece.
        index: Índice del bloque dentro de la página.
    """

    kind: str
    top: float
    bottom: float
    text: str
    left: float = 0.0
    page: int = 0
    index: int = 0


@dataclass
class Section:
    """Agrupa líneas que pertenecen a una misma región visual del documento."""
    lines: list[dict] = field(default_factory=list)
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    bottom: float = 0.0


# ---------------------------------------------------------------------------
# Utilidades comunes
# ---------------------------------------------------------------------------
def clean_text(value: str | None) -> str:
    """Normaliza espacios en blanco y evita valores nulos."""
    return " ".join((value or "").split())


def horizontal_overlap(x0_a: float, x1_a: float, x0_b: float, x1_b: float) -> float:
    """Calcula el ancho de superposición horizontal entre dos rangos."""
    return max(0.0, min(x1_a, x1_b) - max(x0_a, x0_b))


# ===========================================================================
#                          MÓDULO PDF (pdfplumber)
# ===========================================================================
def detect_tables(page):
    """Detecta tablas en una página de PDF.

    Intenta primero con líneas visibles tradicionales.
    Si no, intenta con la estrategia híbrida de precisión (texto vertical + líneas horiz).
    Si falla, cae en la estrategia de puro texto.
    """
    tables = page.find_tables(table_settings=LINE_TABLE_SETTINGS)
    if tables:
        return tables
        
    hybrid_settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "lines",
        "snap_tolerance": 4,
        "intersection_tolerance": 3
    }
    try:
        tables = page.find_tables(table_settings=hybrid_settings)
        if tables:
            return tables
    except Exception:
        pass
        
    return page.find_tables(table_settings=TEXT_TABLE_SETTINGS)


def overlaps_table(word: dict, table_bbox: tuple[float, float, float, float]) -> bool:
    """Indica si una palabra cae dentro del área ocupada por una tabla."""
    x0, top, x1, bottom = table_bbox
    return not (
        word["x1"] <= x0
        or word["x0"] >= x1
        or word["bottom"] <= top
        or word["top"] >= bottom
    )


def filter_words_outside_tables(words: list[dict], tables: list) -> list[dict]:
    """Filtra palabras para conservar solo el texto fuera de tablas."""
    table_boxes = [table.bbox for table in tables]
    return [
        word
        for word in words
        if not any(overlaps_table(word, table_bbox) for table_bbox in table_boxes)
    ]


def line_from_words(words: list[dict]) -> dict:
    """Convierte un grupo de palabras en una línea con límites espaciales."""
    words = sorted(words, key=lambda item: item["x0"])
    return {
        "text": " ".join(clean_text(word["text"]) for word in words).strip(),
        "top": min(word["top"] for word in words),
        "bottom": max(word["bottom"] for word in words),
        "x0": min(word["x0"] for word in words),
        "x1": max(word["x1"] for word in words),
    }


def split_row_into_segments(words: list[dict]) -> list[dict]:
    """Divide una fila en segmentos horizontales.

    Se usa para distinguir cuadros, columnas o bloques separados por
    espacios amplios y así evitar que el texto se mezcle.
    """
    if not words:
        return []

    words = sorted(words, key=lambda item: item["x0"])
    char_widths = [
        (word["x1"] - word["x0"]) / max(len(clean_text(word["text"])), 1)
        for word in words
        if clean_text(word["text"])
    ]
    median_char_width = statistics.median(char_widths) if char_widths else 4
    gap_threshold = max(10, median_char_width * 3.2)

    segments: list[dict] = []
    current = [words[0]]

    for word in words[1:]:
        prev = current[-1]
        gap = max(0.0, word["x0"] - prev["x1"])

        if gap > gap_threshold:
            segments.append(line_from_words(current))
            current = [word]
            continue

        current.append(word)

    if current:
        segments.append(line_from_words(current))

    return [segment for segment in segments if segment["text"]]


def build_lines(words: list[dict], y_tolerance: float = 3) -> list[dict]:
    """Agrupa palabras cercanas verticalmente y forma líneas ordenadas."""
    if not words:
        return []

    words = sorted(words, key=lambda item: (round(item["top"], 1), item["x0"]))
    lines: list[dict] = []
    current_row: list[dict] = [words[0]]
    current_top = words[0]["top"]

    for word in words[1:]:
        if abs(word["top"] - current_top) <= y_tolerance:
            current_row.append(word)
            current_top = statistics.mean(item["top"] for item in current_row)
            continue

        lines.extend(split_row_into_segments(current_row))
        current_row = [word]
        current_top = word["top"]

    if current_row:
        lines.extend(split_row_into_segments(current_row))

    return sorted(
        [line for line in lines if line["text"]],
        key=lambda item: (item["top"], item["x0"]),
    )


def section_gap_limit(section: Section) -> float:
    """Obtiene la distancia vertical máxima para seguir uniendo líneas."""
    heights = [line["bottom"] - line["top"] for line in section.lines]
    return max(16, (statistics.median(heights) if heights else 9) * 1.8)


def can_attach_line(section: Section, line: dict) -> bool:
    """Evalúa si una línea pertenece visualmente a una sección existente."""
    gap = line["top"] - section.bottom
    overlap = horizontal_overlap(line["x0"], line["x1"], section.x0, section.x1)
    aligned_left = abs(line["x0"] - section.x0) <= 18
    aligned_right = abs(line["x1"] - section.x1) <= 18

    return -3 <= gap <= section_gap_limit(section) and (
        overlap > 0 or aligned_left or aligned_right
    )


def append_line_to_section(section: Section, line: dict) -> None:
    """Actualiza una sección agregando una nueva línea y sus límites."""
    section.lines.append(line)
    section.x0 = min(section.x0, line["x0"])
    section.x1 = max(section.x1, line["x1"])
    section.top = min(section.top, line["top"])
    section.bottom = max(section.bottom, line["bottom"])


def build_sections(lines: list[dict], page_number: int) -> list[Block]:
    """Construye secciones de lectura a partir de líneas detectadas.

    Cada sección representa un bloque visual independiente dentro de la página.
    """
    if not lines:
        return []

    sections: list[Section] = []

    for line in sorted(lines, key=lambda item: (item["top"], item["x0"])):
        best_index: int | None = None
        best_score: tuple[float, float, float] | None = None

        for index, section in enumerate(sections):
            if not can_attach_line(section, line):
                continue

            gap = max(0.0, line["top"] - section.bottom)
            overlap = horizontal_overlap(line["x0"], line["x1"], section.x0, section.x1)
            score = (gap, -overlap, abs(line["x0"] - section.x0))

            if best_score is None or score < best_score:
                best_index = index
                best_score = score

        if best_index is None:
            sections.append(
                Section(
                    lines=[line],
                    x0=line["x0"],
                    x1=line["x1"],
                    top=line["top"],
                    bottom=line["bottom"],
                )
            )
            continue

        append_line_to_section(sections[best_index], line)

    blocks: list[Block] = []

    for index, section in enumerate(
        sorted(sections, key=lambda item: (item.top, item.x0)),
        start=1,
    ):
        ordered_lines = sorted(section.lines, key=lambda item: (item["top"], item["x0"]))
        content = "\n".join(
            clean_text(line["text"])
            for line in ordered_lines
            if clean_text(line["text"])
        )

        blocks.append(
            Block(
                kind="section",
                top=section.top,
                bottom=section.bottom,
                left=section.x0,
                page=page_number,
                index=index,
                text=(
                    f"[SECTION_START page={page_number} index={index} "
                    f"x0={section.x0:.1f} x1={section.x1:.1f}]\n"
                    f"{content}\n"
                    f"[SECTION_END]"
                ),
            )
        )

    return blocks


def normalize_bbox(
    bbox: tuple[float, float, float, float] | None,
    page_bbox: tuple[float, float, float, float],
    epsilon: float = 0.001,
) -> tuple[float, float, float, float] | None:
    """Ajusta un bounding box para que siempre quede dentro de la página.

    Corrige pequeños errores de precisión flotante y descarta cajas inválidas.
    """
    if not bbox:
        return None

    px0, ptop, px1, pbottom = page_bbox
    x0, top, x1, bottom = bbox

    x0 = min(max(x0, px0), px1)
    x1 = min(max(x1, px0), px1)
    top = min(max(top, ptop), pbottom)
    bottom = min(max(bottom, ptop), pbottom)

    if abs(x0 - px0) <= epsilon:
        x0 = px0
    if abs(x1 - px1) <= epsilon:
        x1 = px1
    if abs(top - ptop) <= epsilon:
        top = ptop
    if abs(bottom - pbottom) <= epsilon:
        bottom = pbottom

    if x1 <= x0 or bottom <= top:
        return None

    return (x0, top, x1, bottom)


def extract_box_text(page, bbox: tuple[float, float, float, float] | None) -> str:
    """Extrae el texto contenido en una caja específica del PDF."""
    safe_bbox = normalize_bbox(bbox, page.bbox)
    if not safe_bbox:
        return ""

    cropped = page.crop(safe_bbox, strict=False)
    words = cropped.extract_words(
        x_tolerance=1.5,
        y_tolerance=2,
        use_text_flow=False,
        keep_blank_chars=False,
    ) or []
    lines = build_lines(words, y_tolerance=2)

    return "\n".join(
        clean_text(line["text"])
        for line in lines
        if clean_text(line["text"])
    ).strip()


def serialize_table(table, page) -> str:
    """Serializa una tabla preservando filas y celdas.

    Prioriza la lectura por bounding box de cada celda. Si esa estructura no
    está disponible, usa la extracción tabular estándar como respaldo.
    """
    rows_output: list[str] = []
    table_rows = getattr(table, "rows", None) or []

    for row_index, row in enumerate(table_rows, start=1):
        cell_parts: list[str] = []
        for col_index, cell_bbox in enumerate(getattr(row, "cells", []), start=1):
            cell_text = extract_box_text(page, cell_bbox) or "(vacío)"
            cell_parts.append(
                f"[CELL row={row_index} col={col_index}]\n{cell_text}"
            )

        if cell_parts:
            rows_output.append(f"[ROW {row_index}]\n" + "\n".join(cell_parts))

    if rows_output:
        return "\n".join(rows_output)

    rows = table.extract() or []
    fallback_rows = []

    for row_index, row in enumerate(rows, start=1):
        cells = [
            f"[CELL row={row_index} col={col_index}]\n{clean_text(cell) or '(vacío)'}"
            for col_index, cell in enumerate(row or [], start=1)
        ]
        if cells:
            fallback_rows.append(f"[ROW {row_index}]\n" + "\n".join(cells))

    return "\n".join(fallback_rows) if fallback_rows else "(tabla vacía)"


def table_block(table, page, page_number: int, index: int) -> Block:
    """Convierte una tabla detectada en un bloque etiquetado de salida."""
    x0, top, x1, bottom = table.bbox
    content = serialize_table(table, page)

    return Block(
        kind="table",
        top=top,
        bottom=bottom,
        left=x0,
        page=page_number,
        index=index,
        text=(
            f"[TABLE_START page={page_number} index={index}]\n"
            f"{content}\n"
            f"[TABLE_END]"
        ),
    )


def extract_pdf(pdf_path: Path) -> list[Block]:
    """Procesa un PDF utilizando una estrategia híbrida de alta velocidad y precisión.
    
    1. Usa PyMuPDF (fitz) para pre-filtrar de forma instantánea las páginas candidatos
       que contienen palabras clave de interés (comisiones, administración, rendimiento,
       pizarra, serie). Las primeras 3 páginas se procesan siempre para no perder metadatos de cabecera.
    2. Aplica pdfplumber únicamente en esas páginas con tolerancias geométricas optimizadas.
    """
    if pdfplumber is None:
        raise RuntimeError(
            "pdfplumber no está instalado. Ejecuta: pip install pdfplumber"
        )

    all_blocks: list[Block] = []

    # Determinar páginas candidatas usando PyMuPDF (fitz) si está disponible
    target_pages = None
    if fitz is not None:
        try:
            doc = fitz.open(str(pdf_path))
            total_pages = len(doc)
            
            # Si el documento es pequeño, procesamos todo
            if total_pages <= 5:
                target_pages = list(range(1, total_pages + 1))
            else:
                # Páginas 1, 2, 3 siempre se procesan por metadatos
                target_pages = [1, 2, 3]
                keywords = ["comision", "comisión", "administraci", "administración", "rendimiento", "ter", "gasto", "neto", "pizarra", "serie"]
                for page_num in range(3, total_pages):
                    text_space = doc[page_num].get_text("text").lower()
                    if any(kw in text_space for kw in keywords):
                        target_pages.append(page_num + 1) # 1-indexed para pdfplumber
                # Asegurar orden y unicidad
                target_pages = sorted(list(set(target_pages)))
                logger.info(f"Filtro PyMuPDF: {pdf_path.name} reducido de {total_pages} a {len(target_pages)} páginas de interés para extracción de tesis.")
            doc.close()
        except Exception as e:
            logger.warning(f"Error al pre-filtrar con PyMuPDF: {e}. Procesando todas las páginas.")

    with pdfplumber.open(str(pdf_path)) as pdf:
        total_pdf_pages = len(pdf.pages)
        # Si no pudimos pre-filtrar, procesar todo
        pages_to_process = target_pages if target_pages else list(range(1, total_pdf_pages + 1))
        
        for page_number in pages_to_process:
            if page_number > total_pdf_pages:
                continue
            try:
                page = pdf.pages[page_number - 1]
                tables = detect_tables(page)
                words = page.extract_words(
                    x_tolerance=1.5,
                    y_tolerance=2,
                    use_text_flow=False,
                    keep_blank_chars=False,
                ) or []

                text_words = filter_words_outside_tables(words, tables)
                lines = build_lines(text_words)
                sections = build_sections(lines, page_number)
                table_blocks = [
                    table_block(table, page, page_number, index)
                    for index, table in enumerate(tables, start=1)
                ]

                page_blocks = sorted(
                    [*sections, *table_blocks],
                    key=lambda block: (
                        block.top,
                        block.left,
                        0 if block.kind == "section" else 1,
                    ),
                )
                all_blocks.extend(page_blocks)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Error procesando página %s de %s: %s",
                    page_number, pdf_path.name, exc,
                )

    return all_blocks


# ===========================================================================
#                          MÓDULO WORD (python-docx)
# ===========================================================================
def _iter_block_items(parent) -> Iterator:
    """Itera párrafos y tablas en orden de aparición real dentro del documento.

    python-docx no expone esta utilidad directamente, así que recorremos el
    XML subyacente para preservar la posición visual relativa entre párrafos
    y tablas.
    """
    if docx is None:  # pragma: no cover
        raise RuntimeError("python-docx no está instalado.")

    if isinstance(parent, _DocxDocument):
        parent_elm = parent.element.body
    elif isinstance(parent, _DocxCell):
        parent_elm = parent._tc
    else:
        parent_elm = parent

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield _DocxParagraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield _DocxTable(child, parent)


def _docx_paragraph_to_block(
    paragraph,
    page_number: int,
    seq_index: int,
    block_index: int,
) -> Block | None:
    """Convierte un párrafo de Word en un Block de tipo `section`."""
    text = clean_text(paragraph.text)
    if not text:
        return None

    style_name = ""
    try:
        style_name = paragraph.style.name if paragraph.style else ""
    except Exception:  # pragma: no cover
        style_name = ""

    style_tag = f" style=\"{style_name}\"" if style_name else ""

    return Block(
        kind="section",
        top=float(seq_index),
        bottom=float(seq_index),
        left=0.0,
        page=page_number,
        index=block_index,
        text=(
            f"[SECTION_START page={page_number} index={block_index}{style_tag}]\n"
            f"{text}\n"
            f"[SECTION_END]"
        ),
    )


def _docx_table_to_block(
    table,
    page_number: int,
    seq_index: int,
    block_index: int,
) -> Block:
    """Convierte una tabla de Word en un Block de tipo `table`."""
    rows_output: list[str] = []

    # Para evitar duplicar celdas combinadas (merged cells), guardamos los
    # identificadores de los elementos XML ya procesados por fila.
    for row_index, row in enumerate(table.rows, start=1):
        seen_tcs: set[int] = set()
        cell_parts: list[str] = []
        col_index = 0

        for cell in row.cells:
            tc_id = id(cell._tc)
            if tc_id in seen_tcs:
                continue
            seen_tcs.add(tc_id)
            col_index += 1

            # Una celda puede contener párrafos y/o tablas anidadas.
            cell_pieces: list[str] = []
            for item in _iter_block_items(cell):
                if isinstance(item, _DocxParagraph):
                    piece = clean_text(item.text)
                    if piece:
                        cell_pieces.append(piece)
                elif isinstance(item, _DocxTable):
                    nested = []
                    for r_i, nested_row in enumerate(item.rows, start=1):
                        nested_cells = [
                            clean_text(c.text) or "(vacío)"
                            for c in nested_row.cells
                        ]
                        nested.append(f"  [NESTED_ROW {r_i}] " + " | ".join(nested_cells))
                    if nested:
                        cell_pieces.append("\n".join(nested))

            cell_text = "\n".join(cell_pieces) if cell_pieces else "(vacío)"
            cell_parts.append(f"[CELL row={row_index} col={col_index}]\n{cell_text}")

        if cell_parts:
            rows_output.append(f"[ROW {row_index}]\n" + "\n".join(cell_parts))

    content = "\n".join(rows_output) if rows_output else "(tabla vacía)"

    return Block(
        kind="table",
        top=float(seq_index),
        bottom=float(seq_index),
        left=0.0,
        page=page_number,
        index=block_index,
        text=(
            f"[TABLE_START page={page_number} index={block_index}]\n"
            f"{content}\n"
            f"[TABLE_END]"
        ),
    )


def extract_docx(docx_path: Path) -> list[Block]:
    """Procesa un archivo .docx y devuelve la lista de bloques estructurados.

    Word no tiene un concepto de "página" tan estricto como un PDF, así que
    tratamos todo el documento como una sola página lógica (page=1) y usamos
    el orden secuencial real de párrafos/tablas para preservar la lectura.
    """
    if docx is None:
        raise RuntimeError(
            "python-docx no está instalado. Ejecuta: pip install python-docx"
        )

    document = docx.Document(str(docx_path))
    blocks: list[Block] = []

    page_number = 1
    section_counter = 0
    table_counter = 0

    for seq_index, item in enumerate(_iter_block_items(document), start=1):
        if isinstance(item, _DocxParagraph):
            section_counter += 1
            block = _docx_paragraph_to_block(
                item, page_number, seq_index, section_counter
            )
            if block is not None:
                blocks.append(block)
        elif isinstance(item, _DocxTable):
            table_counter += 1
            blocks.append(
                _docx_table_to_block(item, page_number, seq_index, table_counter)
            )

    return blocks


# ===========================================================================
#                          DESPACHO Y FORMATEO DE SALIDA
# ===========================================================================
def extract_txt(txt_path: Path) -> list[Block]:
    """Procesa un archivo de texto plano y lo convierte en bloques de sección."""
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Dividir por páginas si existen marcadores OCR
    pages = re.split(r"--- PÁGINA \d+ ---", content)
    blocks = []
    for i, page_content in enumerate(pages):
        if not page_content.strip(): continue
        blocks.append(Block(
            kind="section",
            top=float(i),
            bottom=float(i),
            text=page_content.strip(),
            page=i+1,
            index=1
        ))
    return blocks

def extract_document(path: Path) -> list[Block]:
    """Despacha la extracción según la extensión del archivo."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".txt":
        return extract_txt(path)
    raise ValueError(
        f"Extensión no soportada: {suffix}. "
        f"Soportadas: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
    )


def blocks_to_txt(blocks: list[Block]) -> str:
    """Serializa los bloques al formato etiquetado original (TXT)."""
    output: list[str] = []
    current_page: int | None = None

    for block in blocks:
        if block.page != current_page:
            if current_page is not None:
                output.append(f"[PAGE_END {current_page}]")
            output.append(f"[PAGE_START {block.page}]")
            current_page = block.page
        if block.text.strip():
            output.append(block.text)

    if current_page is not None:
        output.append(f"[PAGE_END {current_page}]")

    return "\n\n".join(output)


def blocks_to_json(blocks: list[Block]) -> str:
    """Serializa los bloques como JSON estructurado para post-procesamiento."""
    return json.dumps([asdict(block) for block in blocks], ensure_ascii=False, indent=2)


def blocks_to_markdown(blocks: list[Block]) -> str:
    """Convierte los bloques a Markdown legible."""
    lines: list[str] = []
    current_page: int | None = None

    for block in blocks:
        if block.page != current_page:
            lines.append(f"\n## Página {block.page}\n")
            current_page = block.page

        if block.kind == "section":
            # Aislamos el contenido entre las marcas SECTION_START / SECTION_END.
            inner = block.text.split("]\n", 1)[-1].rsplit("\n[SECTION_END]", 1)[0]
            lines.append(inner.strip() + "\n")
        elif block.kind == "table":
            lines.append(f"<!-- Tabla {block.index} -->")
            inner = block.text.split("]\n", 1)[-1].rsplit("\n[TABLE_END]", 1)[0]
            # Reconstrucción simple: cada [ROW] como línea separada.
            md_rows: list[list[str]] = []
            current_row: list[str] = []
            for raw_line in inner.splitlines():
                stripped = raw_line.strip()
                if stripped.startswith("[ROW"):
                    if current_row:
                        md_rows.append(current_row)
                    current_row = []
                elif stripped.startswith("[CELL"):
                    current_row.append("")  # placeholder
                elif stripped and current_row:
                    if current_row[-1]:
                        current_row[-1] += " " + stripped
                    else:
                        current_row[-1] = stripped
            if current_row:
                md_rows.append(current_row)

            if md_rows:
                width = max(len(r) for r in md_rows)
                header = md_rows[0] + [""] * (width - len(md_rows[0]))
                lines.append("| " + " | ".join(header) + " |")
                lines.append("| " + " | ".join(["---"] * width) + " |")
                for r in md_rows[1:]:
                    r_full = r + [""] * (width - len(r))
                    lines.append("| " + " | ".join(r_full) + " |")
                lines.append("")

    return "\n".join(lines).strip() + "\n"


def serialize_blocks(blocks: list[Block], fmt: str) -> str:
    """Convierte una lista de bloques al formato pedido."""
    fmt = fmt.lower()
    if fmt == "txt":
        return blocks_to_txt(blocks)
    if fmt == "json":
        return blocks_to_json(blocks)
    if fmt in {"md", "markdown"}:
        return blocks_to_markdown(blocks)
    raise ValueError(f"Formato de salida no soportado: {fmt}")


def default_output_path(input_path: Path, fmt: str) -> Path:
    """Calcula la ruta de salida por defecto manteniendo el nombre del archivo."""
    extension = {"txt": ".txt", "json": ".json", "md": ".md", "markdown": ".md"}[fmt]
    return input_path.with_suffix(extension)


# ===========================================================================
#                          CLI / Punto de entrada
# ===========================================================================
def iter_input_files(path: Path, batch: bool) -> Iterable[Path]:
    """Devuelve los archivos a procesar respetando el modo --batch."""
    if path.is_dir():
        if not batch:
            raise ValueError(
                f"'{path}' es un directorio. Usa --batch para procesar todos los archivos."
            )
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield child
    else:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Extensión no soportada: {path.suffix}. "
                f"Soportadas: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
        yield path


def process_file(
    input_path: Path,
    output_path: Path | None,
    fmt: str,
) -> Path:
    """Procesa un único archivo y guarda el resultado."""
    logger.info("Procesando %s", input_path)
    blocks = extract_document(input_path)
    serialized = serialize_blocks(blocks, fmt)

    if output_path is None:
        output_path = default_output_path(input_path, fmt)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    logger.info("→ Salida: %s (%d bloques)", output_path, len(blocks))
    return output_path


def main() -> None:
    """Punto de entrada de consola.

    Recibe la ruta de uno o varios documentos, ejecuta la extracción y guarda
    el contenido estructurado en el formato elegido.
    """
    parser = argparse.ArgumentParser(
        description="Extrae texto de PDFs y archivos Word marcando párrafos y tablas.",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Ruta del archivo (.pdf / .docx) o carpeta de entrada.",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Ruta del archivo de salida. Por defecto: mismo nombre con la extensión del formato.",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["txt", "json", "md"],
        default="txt",
        help="Formato de salida (por defecto: txt).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Procesa todos los .pdf y .docx encontrados dentro de un directorio.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Muestra mensajes de log detallados.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    if not args.input.exists():
        parser.error(f"La ruta '{args.input}' no existe.")

    files = list(iter_input_files(args.input, args.batch))
    if not files:
        parser.error("No se encontraron archivos compatibles para procesar.")

    if args.batch and args.output and args.output.suffix:
        parser.error("En modo --batch, --output debe ser un directorio (no un archivo).")

    failures = 0
    for file_path in files:
        try:
            if args.batch:
                out_dir = args.output or file_path.parent
                out = out_dir / default_output_path(file_path, args.format).name
            else:
                out = args.output
            process_file(file_path, out, args.format)
        except Exception as exc:
            failures += 1
            logger.error("Error procesando %s: %s", file_path, exc)

    if failures:
        logger.warning("Finalizado con %d error(es).", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
