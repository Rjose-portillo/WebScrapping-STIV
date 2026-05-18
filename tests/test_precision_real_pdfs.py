#!/usr/bin/env python3
"""
Precision Test Suite — Mide tasas de extraccion del parser contra los 142+ PDFs reales.

Este script evalua la calidad del pipeline document_engine + cnbv_extractor
contra los documentos reales descargados de STIV y HSBC.

Metricas reportadas:
    - Tasa de extraccion por campo (comision_admin, TER, rendimientos, etc.)
    - Clasificacion de errores (parseo fallido, campo vacio, excepcion)
    - Resumen por tipo de documento (DICI vs Prospecto)
    - Resumen por institucion (HSBC, Banorte, BBVA, Scotiabank, etc.)

Uso:
    python tests/test_precision_real_pdfs.py                    # Ejecuta analisis completo
    python tests/test_precision_real_pdfs.py --sample 20        # Solo 20 PDFs aleatorios
    python tests/test_precision_real_pdfs.py --verbose          # Detalle por archivo
    python tests/test_precision_real_pdfs.py --report report.json  # Guardar reporte JSON
"""

import json
import logging
import os
import signal
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


# Per-file timeout handler
class TimeoutException(Exception):
    pass


def _timeout_handler(signum, frame):
    raise TimeoutException("PDF processing exceeded time limit")

# Agregar raiz del proyecto al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.parser.cnbv_extractor import CNBVExtractor

# ---------------------------------------------------------------------------
# Configuracion de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("precision_test")


# ---------------------------------------------------------------------------
# Estructuras de datos para metricas
# ---------------------------------------------------------------------------
@dataclass
class ExtractionResult:
    """Resultado de extraccion de un archivo individual."""
    file_path: str
    file_name: str
    tipo_documento: str  # DICI o Prospecto
    institucion: str
    success: bool
    error_message: Optional[str] = None
    processing_time_s: float = 0.0
    # Campos extraidos (True si tiene valor no nulo)
    has_clave_pizarra: bool = False
    has_serie: bool = False
    has_categoria: bool = False
    has_tipo_admin: bool = False
    has_benchmark: bool = False
    has_horizonte: bool = False
    has_comision_admin: bool = False
    has_comision_desempeno: bool = False
    has_ter: bool = False
    has_var_max: bool = False
    has_var_prom: bool = False
    has_volatilidad: bool = False
    has_calif_riesgo: bool = False
    has_rend_1m: bool = False
    has_rend_3m: bool = False
    has_rend_12m: bool = False
    has_rend_3y: bool = False
    has_bench_1m: bool = False
    has_bench_3m: bool = False
    has_bench_12m: bool = False
    has_bench_3y: bool = False
    has_fecha_corte: bool = False
    # Valores crudos para inspeccion
    raw_values: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PrecisionReport:
    """Reporte consolidado de precision."""
    total_files: int = 0
    successful_extractions: int = 0
    failed_extractions: int = 0
    total_processing_time_s: float = 0.0
    # Tasas por campo (porcentaje de extraccion exitosa)
    field_rates: Dict[str, float] = field(default_factory=dict)
    # Desglose por tipo de documento
    by_tipo_doc: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Desglose por institucion
    by_institucion: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # Errores agrupados
    error_categories: Dict[str, int] = field(default_factory=dict)
    # Archivos fallidos detallados
    failed_files: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def infer_institucion(file_path: str) -> str:
    """Infiere la institucion a partir de la ruta del archivo."""
    path_upper = file_path.upper()
    if "HSBC" in path_upper or "HSBCBAL" in path_upper or "HSBGOB" in path_upper:
        return "HSBC"
    if "BBVA" in path_upper:
        return "BBVA"
    if "BANORTE" in path_upper or "NTE" in path_upper.split("/")[-1][:3]:
        return "Banorte"
    if "SCOT" in path_upper:
        return "Scotiabank"
    if "FRANKLIN" in path_upper or "FT-" in path_upper:
        return "Franklin_Templeton"
    if "BLACKROCK" in path_upper or "BLK" in path_upper:
        return "BlackRock"
    if "BNP" in path_upper or "SMX" in path_upper:
        return "BNP_Paribas"
    if "ACTINVER" in path_upper or "ACTIG" in path_upper or "IMPULSA" in path_upper:
        return "Actinver"
    if "AZIMUT" in path_upper or "AZ" in path_upper.split("/")[-1][:2]:
        return "Azimut"
    if "INVEX" in path_upper:
        return "Invex"
    if "CI FONDOS" in path_upper or "CIEQUS" in path_upper or "CIUSD" in path_upper:
        return "CI_Fondos"
    if "SURA" in path_upper or "SUR1E" in path_upper or "FONDEO" in path_upper:
        return "SURA"
    if "VALMEX" in path_upper or "VALMX" in path_upper:
        return "Valmex"
    if "VECTOR" in path_upper or "VECT" in path_upper:
        return "Vector"
    if "INTERCAM" in path_upper or "NHYD" in path_upper:
        return "Intercam"
    if "SAMCAP" in path_upper:
        return "SAM"
    if "STRGOB" in path_upper:
        return "Estrategia"
    return "Otra"


def infer_tipo_documento(file_path: str) -> str:
    """Infiere el tipo de documento (DICI o Prospecto)."""
    name_upper = Path(file_path).name.upper()
    if "DICI" in name_upper or "DOCUMENTO_CLAVE" in name_upper or "DOCTO" in name_upper:
        return "DICI"
    if "PROSPECTO" in name_upper:
        return "Prospecto"
    # Si no es claro, revisar el path
    if "DICI" in file_path.upper():
        return "DICI"
    return "Otro"


def collect_pdf_files(archivos_dir: str, max_size_mb: float = 5.0) -> List[str]:
    """Recolecta todos los PDFs del directorio Archivos/ bajo un tamaño maximo."""
    pdf_files: List[str] = []
    max_bytes = int(max_size_mb * 1024 * 1024)
    for root, _, files in os.walk(archivos_dir):
        for f in files:
            if f.lower().endswith(".pdf"):
                full_path = os.path.join(root, f)
                try:
                    if os.path.getsize(full_path) <= max_bytes:
                        pdf_files.append(full_path)
                except OSError:
                    pass
    return sorted(pdf_files)


# ---------------------------------------------------------------------------
# Motor de evaluacion
# ---------------------------------------------------------------------------
def evaluate_single_pdf(extractor: CNBVExtractor, pdf_path: str, timeout_s: int = 60) -> ExtractionResult:
    """Ejecuta el extractor contra un PDF individual y mide resultados."""
    file_name = Path(pdf_path).name
    tipo_doc = infer_tipo_documento(pdf_path)
    institucion = infer_institucion(pdf_path)

    result = ExtractionResult(
        file_path=pdf_path,
        file_name=file_name,
        tipo_documento=tipo_doc,
        institucion=institucion,
        success=False,
    )

    start_time = time.time()
    # Set per-file timeout
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout_s)
    try:
        data = extractor.extract(pdf_path)
        elapsed = time.time() - start_time
        result.processing_time_s = elapsed

        if "error" in data:
            result.error_message = data["error"]
            return result

        result.success = True

        # Evaluar campos de fondo/serie
        fondo = data.get("fondo_serie", {})
        result.has_clave_pizarra = bool(
            fondo.get("clave_pizarra") and fondo["clave_pizarra"] != "DESCONOCIDO"
        )
        result.has_serie = bool(
            fondo.get("serie_accionaria") and fondo["serie_accionaria"] != "Unica"
        )
        result.has_categoria = bool(fondo.get("categoria"))
        result.has_tipo_admin = bool(fondo.get("tipo_administracion"))
        result.has_benchmark = bool(fondo.get("benchmark_oficial"))
        result.has_horizonte = bool(fondo.get("horizonte_inversion"))

        # Evaluar comisiones
        costos = data.get("estructura_costos", {})
        result.has_comision_admin = bool(costos.get("comision_administracion_anual"))
        result.has_comision_desempeno = bool(costos.get("comision_desempeno"))
        result.has_ter = bool(costos.get("gastos_totales_ter"))

        # Evaluar metricas de riesgo
        riesgo = data.get("metricas_riesgo", {})
        result.has_var_max = bool(riesgo.get("var_maximo_autorizado"))
        result.has_var_prom = bool(riesgo.get("var_promedio_observado"))
        result.has_volatilidad = bool(riesgo.get("volatilidad_historica"))
        result.has_calif_riesgo = bool(riesgo.get("calificacion_riesgo_mercado"))

        # Evaluar rendimientos
        rend = data.get("rendimientos_historicos", {})
        periodos = rend.get("periodos", {})
        result.has_rend_1m = bool(periodos.get("1_mes"))
        result.has_rend_3m = bool(periodos.get("3_meses"))
        result.has_rend_12m = bool(periodos.get("12_meses"))
        result.has_rend_3y = bool(periodos.get("3_anios"))

        benchmarks = rend.get("benchmark", {})
        result.has_bench_1m = bool(benchmarks.get("1_mes"))
        result.has_bench_3m = bool(benchmarks.get("3_meses"))
        result.has_bench_12m = bool(benchmarks.get("12_meses"))
        result.has_bench_3y = bool(benchmarks.get("3_anios"))

        result.has_fecha_corte = bool(rend.get("fecha_corte"))

        # Guardar valores crudos para inspeccion
        result.raw_values = {
            "clave_pizarra": fondo.get("clave_pizarra"),
            "serie": fondo.get("serie_accionaria"),
            "categoria": fondo.get("categoria"),
            "tipo_admin": fondo.get("tipo_administracion"),
            "comision_admin": costos.get("comision_administracion_anual"),
            "comision_desempeno": costos.get("comision_desempeno"),
            "ter": costos.get("gastos_totales_ter"),
            "rend_12m": periodos.get("12_meses"),
            "bench_12m": benchmarks.get("12_meses"),
            "var_max": riesgo.get("var_maximo_autorizado"),
        }

    except TimeoutException:
        elapsed = time.time() - start_time
        result.processing_time_s = elapsed
        result.error_message = f"TimeoutException: exceeded {timeout_s}s limit"
        logger.warning(f"Timeout procesando {file_name} (>{timeout_s}s)")
    except Exception as e:
        elapsed = time.time() - start_time
        result.processing_time_s = elapsed
        result.error_message = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning(f"Error procesando {file_name}: {result.error_message}")
    finally:
        signal.alarm(0)  # Cancel alarm
        signal.signal(signal.SIGALRM, old_handler)

    return result


def compute_report(results: List[ExtractionResult]) -> PrecisionReport:
    """Computa el reporte de precision agregado a partir de los resultados individuales."""
    report = PrecisionReport()
    report.total_files = len(results)
    report.successful_extractions = sum(1 for r in results if r.success)
    report.failed_extractions = sum(1 for r in results if not r.success)
    report.total_processing_time_s = sum(r.processing_time_s for r in results)

    # Tasas globales por campo
    field_names = [
        "has_clave_pizarra", "has_serie", "has_categoria", "has_tipo_admin",
        "has_benchmark", "has_horizonte", "has_comision_admin",
        "has_comision_desempeno", "has_ter", "has_var_max", "has_var_prom",
        "has_volatilidad", "has_calif_riesgo", "has_rend_1m", "has_rend_3m",
        "has_rend_12m", "has_rend_3y", "has_bench_1m", "has_bench_3m",
        "has_bench_12m", "has_bench_3y", "has_fecha_corte",
    ]

    successful = [r for r in results if r.success]
    n_success = len(successful) or 1  # avoid division by zero

    for field_name in field_names:
        count = sum(1 for r in successful if getattr(r, field_name, False))
        report.field_rates[field_name.replace("has_", "")] = round(
            100.0 * count / n_success, 1
        )

    # Desglose por tipo de documento
    by_tipo: Dict[str, List[ExtractionResult]] = defaultdict(list)
    for r in successful:
        by_tipo[r.tipo_documento].append(r)

    for tipo, tipo_results in by_tipo.items():
        n = len(tipo_results) or 1
        report.by_tipo_doc[tipo] = {}
        for field_name in field_names:
            count = sum(1 for r in tipo_results if getattr(r, field_name, False))
            report.by_tipo_doc[tipo][field_name.replace("has_", "")] = round(
                100.0 * count / n, 1
            )

    # Desglose por institucion
    by_inst: Dict[str, List[ExtractionResult]] = defaultdict(list)
    for r in successful:
        by_inst[r.institucion].append(r)

    for inst, inst_results in by_inst.items():
        n = len(inst_results) or 1
        report.by_institucion[inst] = {
            "total_files": len(inst_results),
            "comision_admin_rate": round(
                100.0 * sum(1 for r in inst_results if r.has_comision_admin) / n, 1
            ),
            "ter_rate": round(
                100.0 * sum(1 for r in inst_results if r.has_ter) / n, 1
            ),
            "rend_12m_rate": round(
                100.0 * sum(1 for r in inst_results if r.has_rend_12m) / n, 1
            ),
            "categoria_rate": round(
                100.0 * sum(1 for r in inst_results if r.has_categoria) / n, 1
            ),
        }

    # Errores agrupados
    errors: Dict[str, int] = defaultdict(int)
    for r in results:
        if not r.success and r.error_message:
            # Agrupar por tipo de error
            error_type = r.error_message.split(":")[0] if ":" in r.error_message else r.error_message[:50]
            errors[error_type] += 1
    report.error_categories = dict(errors)

    # Archivos fallidos
    report.failed_files = [r.file_name for r in results if not r.success]

    return report


# ---------------------------------------------------------------------------
# Salida formateada
# ---------------------------------------------------------------------------
def print_report(report: PrecisionReport, verbose_results: Optional[List[ExtractionResult]] = None) -> None:
    """Imprime el reporte en formato legible."""
    print("\n" + "=" * 80)
    print("   REPORTE DE PRECISION — PIPELINE DE EXTRACCION CNBV")
    print("=" * 80)

    print(f"\n📊 RESUMEN GENERAL:")
    print(f"   Total PDFs procesados:      {report.total_files}")
    print(f"   Extracciones exitosas:      {report.successful_extractions} "
          f"({100*report.successful_extractions/max(report.total_files,1):.1f}%)")
    print(f"   Extracciones fallidas:      {report.failed_extractions}")
    print(f"   Tiempo total procesamiento: {report.total_processing_time_s:.1f}s "
          f"({report.total_processing_time_s/max(report.total_files,1):.2f}s/archivo)")

    print(f"\n📈 TASAS DE EXTRACCION POR CAMPO (sobre {report.successful_extractions} exitosos):")
    print(f"   {'Campo':<25} {'Tasa':<8} {'Barra'}")
    print(f"   {'-'*25} {'-'*8} {'-'*30}")

    # Campos de interes principal (para tesis)
    priority_fields = [
        ("comision_admin", "Comision Admin"),
        ("ter", "TER (Gastos Totales)"),
        ("comision_desempeno", "Comision Desempeño"),
        ("rend_12m", "Rendimiento 12M"),
        ("rend_1m", "Rendimiento 1M"),
        ("rend_3m", "Rendimiento 3M"),
        ("rend_3y", "Rendimiento 3 Años"),
        ("bench_12m", "Benchmark 12M"),
        ("categoria", "Categoria"),
        ("tipo_admin", "Tipo Administracion"),
        ("benchmark", "Benchmark Oficial"),
        ("var_max", "VaR Maximo"),
        ("clave_pizarra", "Clave Pizarra"),
        ("horizonte", "Horizonte Inversion"),
        ("fecha_corte", "Fecha de Corte"),
    ]

    for field_key, label in priority_fields:
        rate = report.field_rates.get(field_key, 0.0)
        bar_len = int(rate / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        color = "🟢" if rate >= 60 else "🟡" if rate >= 30 else "🔴"
        print(f"   {color} {label:<23} {rate:>5.1f}%  {bar}")

    print(f"\n🏦 DESGLOSE POR INSTITUCION:")
    print(f"   {'Institucion':<20} {'#Docs':<6} {'Comision%':<10} {'TER%':<8} {'Rend12M%':<10} {'Categ%'}")
    print(f"   {'-'*20} {'-'*6} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")
    for inst, data in sorted(report.by_institucion.items(), key=lambda x: -x[1].get("total_files", 0)):
        print(
            f"   {inst:<20} {data['total_files']:<6} "
            f"{data['comision_admin_rate']:>7.1f}%  "
            f"{data['ter_rate']:>5.1f}%  "
            f"{data['rend_12m_rate']:>7.1f}%  "
            f"{data['categoria_rate']:>5.1f}%"
        )

    if report.by_tipo_doc:
        print(f"\n📄 DESGLOSE POR TIPO DE DOCUMENTO:")
        for tipo, rates in report.by_tipo_doc.items():
            print(f"\n   [{tipo}] (Campos clave):")
            for field_key, label in priority_fields[:8]:
                rate = rates.get(field_key, 0.0)
                print(f"      {label:<23} {rate:>5.1f}%")

    if report.error_categories:
        print(f"\n❌ ERRORES AGRUPADOS:")
        for error_type, count in sorted(report.error_categories.items(), key=lambda x: -x[1]):
            print(f"   {error_type}: {count}")

    if report.failed_files:
        print(f"\n⚠️  ARCHIVOS FALLIDOS ({len(report.failed_files)}):")
        for f in report.failed_files[:15]:
            print(f"   - {f}")
        if len(report.failed_files) > 15:
            print(f"   ... y {len(report.failed_files) - 15} mas")

    print("\n" + "=" * 80)

    # Impresion detallada por archivo (si verbose)
    if verbose_results:
        print("\n📋 DETALLE POR ARCHIVO:")
        for r in verbose_results:
            if r.success:
                vals = r.raw_values
                status = "✅"
                detail = (
                    f"  Pizarra={vals.get('clave_pizarra', '-')}, "
                    f"TER={vals.get('ter', '-')}, "
                    f"Admin={vals.get('comision_admin', '-')}, "
                    f"R12M={vals.get('rend_12m', '-')}, "
                    f"Cat={vals.get('categoria', '-')}"
                )
            else:
                status = "❌"
                detail = f"  ERROR: {r.error_message}"
            print(f"   {status} [{r.tipo_documento:<9}] {r.file_name[:60]}")
            print(f"      {detail}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Precision test: extractor vs 142+ real PDFs"
    )
    parser.add_argument(
        "--archivos-dir", default="Archivos",
        help="Directorio con los PDFs descargados (default: Archivos)"
    )
    parser.add_argument(
        "--sample", type=int, default=0,
        help="Procesar solo N archivos aleatorios (0 = todos)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Mostrar detalle por archivo"
    )
    parser.add_argument(
        "--report", type=str, default="",
        help="Guardar reporte JSON en esta ruta"
    )
    parser.add_argument(
        "--filter-inst", type=str, default="",
        help="Filtrar por institucion (ej: HSBC, Banorte)"
    )
    parser.add_argument(
        "--filter-type", type=str, default="",
        help="Filtrar por tipo de documento (DICI o Prospecto)"
    )

    args = parser.parse_args()

    # Recolectar PDFs
    pdf_files = collect_pdf_files(args.archivos_dir)
    if not pdf_files:
        print(f"❌ No se encontraron PDFs en '{args.archivos_dir}'")
        sys.exit(1)

    print(f"🔍 Encontrados {len(pdf_files)} PDFs en '{args.archivos_dir}'")

    # Aplicar filtros
    if args.filter_inst:
        pdf_files = [f for f in pdf_files if args.filter_inst.upper() in f.upper()]
        print(f"   Filtrado por institucion '{args.filter_inst}': {len(pdf_files)} PDFs")

    if args.filter_type:
        pdf_files = [
            f for f in pdf_files
            if infer_tipo_documento(f).upper() == args.filter_type.upper()
        ]
        print(f"   Filtrado por tipo '{args.filter_type}': {len(pdf_files)} PDFs")

    # Sampling
    if args.sample > 0 and args.sample < len(pdf_files):
        import random
        random.seed(42)
        pdf_files = random.sample(pdf_files, args.sample)
        print(f"   Muestra aleatoria: {len(pdf_files)} PDFs")

    # Ejecutar extraccion
    print(f"\n⚙️  Ejecutando extractor contra {len(pdf_files)} PDFs...")
    extractor = CNBVExtractor()
    results: List[ExtractionResult] = []

    for i, pdf_path in enumerate(pdf_files, 1):
        if i % 10 == 0 or i == 1:
            print(f"   Procesando {i}/{len(pdf_files)}: {Path(pdf_path).name[:50]}...")
        result = evaluate_single_pdf(extractor, pdf_path)
        results.append(result)

    # Computar reporte
    report = compute_report(results)

    # Imprimir
    print_report(report, verbose_results=results if args.verbose else None)

    # Guardar JSON si se solicita
    if args.report:
        report_dict = {
            "summary": {
                "total_files": report.total_files,
                "successful": report.successful_extractions,
                "failed": report.failed_extractions,
                "total_time_s": report.total_processing_time_s,
            },
            "field_rates": report.field_rates,
            "by_tipo_doc": report.by_tipo_doc,
            "by_institucion": report.by_institucion,
            "errors": report.error_categories,
            "failed_files": report.failed_files,
            "detailed_results": [
                {
                    "file": r.file_name,
                    "tipo": r.tipo_documento,
                    "inst": r.institucion,
                    "success": r.success,
                    "time_s": round(r.processing_time_s, 2),
                    "values": r.raw_values if r.success else {},
                    "error": r.error_message,
                }
                for r in results
            ],
        }
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Reporte JSON guardado en: {args.report}")

    # Exit code basado en tasa de exito
    success_rate = report.successful_extractions / max(report.total_files, 1)
    if success_rate < 0.5:
        print("\n⚠️  TASA DE EXITO < 50% — Se recomienda revisar el parser")
        sys.exit(2)


if __name__ == "__main__":
    main()
