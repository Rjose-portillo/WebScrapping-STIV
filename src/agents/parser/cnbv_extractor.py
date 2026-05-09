import json
import logging
import re
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import asdict
from src.agents.parser.document_engine import extract_document, Block

logger = logging.getLogger(__name__)

class CNBVExtractor:
    """
    Extractor de alta precisión para prospectos de inversión de la CNBV (Anexo 2 CUFI).
    Transforma bloques de texto y tablas en un esquema JSON relacional.
    """

    def __init__(self):
        # Patrones comunes para búsqueda de campos
        self.patterns = {
            "clave_pizarra": re.compile(r"Clave\s+de\s+Pizarra:\s*([A-Z0-9_\-]+)", re.IGNORECASE),
            "serie": re.compile(r"Serie\s+Accionaria:\s*([A-Z0-9_\-]+)", re.IGNORECASE),
            "categoria": re.compile(r"Categoría:\s*(Renta\s+Variable|Deuda)", re.IGNORECASE),
            "tipo_admin": re.compile(r"Tipo\s+de\s+Administración:\s*(Activa|Pasiva)", re.IGNORECASE),
            "horizonte": re.compile(r"Horizonte\s+de\s+Inversión\s+sugerido:\s*([^\.]+)", re.IGNORECASE),
            "var_max": re.compile(r"VaR\s+máximo\s+autorizado:\s*([\d\.]+%?)", re.IGNORECASE),
            "var_prom": re.compile(r"VaR\s+promedio\s+observado:\s*([\d\.]+%?)", re.IGNORECASE),
            "calificacion_crediticia": re.compile(r"Calificación\s+crediticia:\s*([A-Z\d]+(?:\/[A-Z\d]+)?)", re.IGNORECASE),
            "calificacion_riesgo": re.compile(r"Calificación\s+de\s+riesgo\s+de\s+mercado:\s*(\d)", re.IGNORECASE),
            "comision_admin": re.compile(r"Comisión\s+por\s+administración\s+anual:\s*([\d\.]+%?)", re.IGNORECASE),
            "comision_desempeno": re.compile(r"Comisión\s+por\s+desempeño:\s*([\d\.]+%?)", re.IGNORECASE),
            "ter": re.compile(r"Gastos\s+totales\s*\(TER\):\s*([\d\.]+%?)", re.IGNORECASE),
            "fecha_corte": re.compile(r"Fecha\s+de\s+corte:\s*(\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4})", re.IGNORECASE),
        }

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Genera un hash SHA-256 para trazabilidad."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _extract_from_text(self, text: str, pattern: re.Pattern) -> Optional[str]:
        match = pattern.search(text)
        return match.group(1).strip() if match else None

    def _find_in_blocks(self, blocks: List[Block], key: str) -> Optional[str]:
        pattern = self.patterns.get(key)
        if not pattern:
            return None
        for block in blocks:
            found = self._extract_from_text(block.text, pattern)
            if found:
                return found
        return None

    def _extract_rendimientos(self, blocks: List[Block]) -> Dict[str, Any]:
        """Busca y extrae la tabla de rendimientos netos."""
        rendimientos = {
            "periodos": {
                "1_mes": None,
                "3_meses": None,
                "12_meses": None,
                "3_anios": None
            },
            "benchmark": {
                "1_mes": None,
                "3_meses": None,
                "12_meses": None,
                "3_anios": None
            },
            "fecha_corte": None
        }

        # Buscamos la tabla de rendimientos
        for block in blocks:
            if block.kind == "table":
                content = block.text.lower()
                if "rendimientos netos" in content or "desempeño histórico" in content:
                    # Lógica simplificada de extracción de celdas
                    # En una implementación real, aquí navegaríamos por las celdas [CELL row=X col=Y]
                    # Por ahora, buscamos patrones dentro del bloque de la tabla
                    rendimientos["fecha_corte"] = self._extract_from_text(block.text, self.patterns["fecha_corte"])
                    
                    # Intentar capturar valores numéricos cerca de palabras clave
                    # (Esto es un placeholder; la lógica robusta usaría coordenadas de celdas)
                    if not rendimientos["fecha_corte"]:
                        # A veces la fecha está justo antes de la tabla
                        pass

        return rendimientos

    def extract(self, file_path: str, url_stiv: str = "Desconocido") -> Dict[str, Any]:
        """
        Procesa el PDF y retorna el JSON estructurado según los requerimientos de tesis.
        """
        path = Path(file_path)
        logger.info(f"Iniciando extracción estructurada de: {path.name}")

        # 1. Obtener bloques estructurales
        try:
            blocks = extract_document(path)
        except Exception as e:
            logger.error(f"Error al procesar estructura del documento {file_path}: {e}")
            return {"error": str(e)}

        # 2. Mapeo a Entidades
        data = {
            "metadata": {
                "hash_archivo": self._calculate_file_hash(path),
                "url_stiv": url_stiv,
                "nombre_archivo": path.name
            },
            "fondo_serie": {
                "clave_pizarra": self._find_in_blocks(blocks, "clave_pizarra"),
                "serie_accionaria": self._find_in_blocks(blocks, "serie"),
                "categoria": self._find_in_blocks(blocks, "categoria"),
                "tipo_administracion": self._find_in_blocks(blocks, "tipo_admin"),
                "benchmark_oficial": self._find_in_blocks(blocks, "benchmark_oficial"), # Necesita búsqueda semántica
                "horizonte_inversion": self._find_in_blocks(blocks, "horizonte")
            },
            "metricas_riesgo": {
                "var_maximo_autorizado": self._find_in_blocks(blocks, "var_max"),
                "var_promedio_observado": self._find_in_blocks(blocks, "var_prom"),
                "calificacion_crediticia": self._find_in_blocks(blocks, "calificacion_crediticia"),
                "calificacion_riesgo_mercado": self._find_in_blocks(blocks, "calificacion_riesgo")
            },
            "estructura_costos": {
                "comision_administracion_anual": self._find_in_blocks(blocks, "comision_admin"),
                "comision_desempeno": self._find_in_blocks(blocks, "comision_desempeno"),
                "gastos_totales_ter": self._find_in_blocks(blocks, "ter")
            },
            "rendimientos_historicos": self._extract_rendimientos(blocks)
        }

        # Validación crítica: Fecha de Corte
        if not data["rendimientos_historicos"]["fecha_corte"]:
             # Búsqueda global si no se encontró en la tabla
             data["rendimientos_historicos"]["fecha_corte"] = self._find_in_blocks(blocks, "fecha_corte")

        logger.info(f"Extracción completada para {path.name}. Fecha Corte: {data['rendimientos_historicos']['fecha_corte']}")
        return data

    def save_json(self, data: Dict[str, Any], output_path: str):
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info(f"Resultado guardado en: {output_path}")

if __name__ == "__main__":
    # Prueba rápida
    import sys
    if len(sys.argv) > 1:
        extractor = CNBVExtractor()
        res = extractor.extract(sys.argv[1])
        print(json.dumps(res, indent=2, ensure_ascii=False))
