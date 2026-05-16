"""
Gestor de base de datos SQLite para el analisis historico de prospectos.

Implementa:
    - Esquema relacional con tablas para instituciones, fondos, series,
      documentos, metricas y rendimientos.
    - Validaciones de integridad y deduplicacion mediante hashes SHA-256.
    - Migraciones automaticas para agregar columnas nuevas sin perder datos.
    - Consultas analiticas para el dashboard de tesis.
"""

import sqlite3
import logging
import os
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Gestor de base de datos para el analisis historico de prospectos.
    Implementa validaciones de integridad, evita duplicados mediante hashes
    y organiza la informacion para analisis comparativo de tesis.
    """

    def __init__(self, db_path: str = "data/tesis_prospectos.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._migrate_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Crea una conexion a la base de datos con Row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa el esquema de la base de datos con restricciones de integridad."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Instituciones (HSBC, CNBV/Operadoras)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS instituciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre TEXT UNIQUE NOT NULL,
                    tipo TEXT
                )
            ''')

            # 2. Fondos
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS fondos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    institucion_id INTEGER,
                    clave_pizarra TEXT UNIQUE NOT NULL,
                    nombre TEXT,
                    categoria TEXT,
                    FOREIGN KEY (institucion_id) REFERENCES instituciones(id)
                )
            ''')

            # 3. Series
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fondo_id INTEGER,
                    serie_accionaria TEXT NOT NULL,
                    UNIQUE(fondo_id, serie_accionaria),
                    FOREIGN KEY (fondo_id) REFERENCES fondos(id)
                )
            ''')

            # 4. Documentos (Versiones de Prospectos/DICI)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS documentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serie_id INTEGER,
                    tipo_documento TEXT NOT NULL,
                    fecha_consulta DATETIME DEFAULT CURRENT_TIMESTAMP,
                    version TEXT,
                    hash_archivo TEXT UNIQUE NOT NULL,
                    ruta_archivo TEXT,
                    url_origen TEXT,
                    FOREIGN KEY (serie_id) REFERENCES series(id)
                )
            ''')

            # 5. Metricas Extraidas (Para comparacion de tesis)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metricas_prospecto (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    documento_id INTEGER UNIQUE,
                    tipo_administracion TEXT,
                    horizonte_inversion TEXT,
                    benchmark_oficial TEXT,
                    var_maximo_autorizado TEXT,
                    var_promedio_observado TEXT,
                    calificacion_crediticia TEXT,
                    calificacion_riesgo_mercado INTEGER,
                    comision_administracion_anual TEXT,
                    comision_desempeno TEXT,
                    gastos_totales_ter TEXT,
                    FOREIGN KEY (documento_id) REFERENCES documentos(id)
                )
            ''')

            # 6. Rendimientos Historicos (por periodo, diferenciando Fondo vs Benchmark)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rendimientos_historicos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    documento_id INTEGER,
                    periodo TEXT NOT NULL,
                    valor_rendimiento TEXT,
                    valor_benchmark TEXT,
                    fecha_corte TEXT,
                    UNIQUE(documento_id, periodo),
                    FOREIGN KEY (documento_id) REFERENCES documentos(id)
                )
            ''')

            conn.commit()
            logger.info("Base de datos inicializada correctamente.")

    def _migrate_db(self):
        """Ejecuta migraciones para agregar columnas nuevas a tablas existentes.

        Esto permite que bases de datos creadas con versiones anteriores del codigo
        reciban las columnas nuevas sin perder datos.
        """
        migrations = [
            # (tabla, columna, tipo_sql)
            ("metricas_prospecto", "comision_desempeno", "TEXT"),
            ("metricas_prospecto", "benchmark_oficial", "TEXT"),
            ("metricas_prospecto", "var_promedio_observado", "TEXT"),
            ("metricas_prospecto", "calificacion_crediticia", "TEXT"),
            ("documentos", "url_origen", "TEXT"),
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            for table, column, col_type in migrations:
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                    logger.info(f"Migracion: columna '{column}' agregada a '{table}'.")
                except sqlite3.OperationalError:
                    # La columna ya existe — ignorar
                    pass
            conn.commit()

    # -------------------------------------------------------------------
    # PERSISTENCIA DE EXTRACCION
    # -------------------------------------------------------------------
    def save_extraction_result(self, data: Dict[str, Any], institution_name: str) -> bool:
        """Guarda el resultado de una extraccion completa.

        Valida existencia de institucion, fondo y serie; evita duplicados por hash.
        Persiste comisiones (admin, desempeno, TER) y rendimientos por periodo.

        Args:
            data: Diccionario con estructura del extractor CNBV.
            institution_name: Nombre de la institucion (ej: 'HSBC', 'CNBV_STIV').

        Returns:
            True si se guardo exitosamente, False si hubo error o duplicado.
        """
        if "error" in data:
            logger.warning(f"Datos con error, no se guardan: {data['error']}")
            return False

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Asegurar Institucion
                cursor.execute(
                    "INSERT OR IGNORE INTO instituciones (nombre) VALUES (?)",
                    (institution_name,))
                cursor.execute(
                    "SELECT id FROM instituciones WHERE nombre = ?",
                    (institution_name,))
                inst_id = cursor.fetchone()[0]

                # 2. Asegurar Fondo
                clave = data.get("fondo_serie", {}).get("clave_pizarra")
                if not clave:
                    logger.warning("No se encontro clave de pizarra. Saltando documento.")
                    return False

                categoria = data.get("fondo_serie", {}).get("categoria")
                cursor.execute('''
                    INSERT OR IGNORE INTO fondos (institucion_id, clave_pizarra, categoria)
                    VALUES (?, ?, ?)
                ''', (inst_id, clave, categoria))
                cursor.execute(
                    "SELECT id FROM fondos WHERE clave_pizarra = ?", (clave,))
                fondo_id = cursor.fetchone()[0]

                # Actualizar categoria si cambio
                if categoria:
                    cursor.execute(
                        "UPDATE fondos SET categoria = ? WHERE id = ? AND categoria IS NULL",
                        (categoria, fondo_id))

                # 3. Asegurar Serie
                serie_acc = data.get("fondo_serie", {}).get("serie_accionaria") or "N/A"
                cursor.execute('''
                    INSERT OR IGNORE INTO series (fondo_id, serie_accionaria)
                    VALUES (?, ?)
                ''', (fondo_id, serie_acc))
                cursor.execute(
                    "SELECT id FROM series WHERE fondo_id = ? AND serie_accionaria = ?",
                    (fondo_id, serie_acc))
                serie_id = cursor.fetchone()[0]

                # 4. Insertar Documento (Validacion por hash)
                metadata = data.get("metadata", {})
                file_hash = metadata.get("hash_archivo", "")
                if not file_hash:
                    logger.warning("Hash de archivo vacio. Saltando.")
                    return False

                try:
                    cursor.execute('''
                        INSERT INTO documentos (
                            serie_id, tipo_documento, hash_archivo,
                            ruta_archivo, url_origen
                        ) VALUES (?, ?, ?, ?, ?)
                    ''', (
                        serie_id, "Prospecto", file_hash,
                        metadata.get("nombre_archivo"),
                        metadata.get("url_stiv"),
                    ))
                    doc_id = cursor.lastrowid
                except sqlite3.IntegrityError:
                    logger.warning(
                        f"Documento duplicado saltado (Hash: {file_hash[:10]}...)")
                    return False

                # 5. Insertar Metricas (incluyendo comisiones completas)
                fondo = data.get("fondo_serie", {})
                riesgo = data.get("metricas_riesgo", {})
                costos = data.get("estructura_costos", {})

                cursor.execute('''
                    INSERT INTO metricas_prospecto (
                        documento_id,
                        tipo_administracion,
                        horizonte_inversion,
                        benchmark_oficial,
                        var_maximo_autorizado,
                        var_promedio_observado,
                        calificacion_crediticia,
                        calificacion_riesgo_mercado,
                        comision_administracion_anual,
                        comision_desempeno,
                        gastos_totales_ter
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    doc_id,
                    fondo.get("tipo_administracion"),
                    fondo.get("horizonte_inversion"),
                    fondo.get("benchmark_oficial"),
                    riesgo.get("var_maximo_autorizado"),
                    riesgo.get("var_promedio_observado"),
                    riesgo.get("calificacion_crediticia"),
                    riesgo.get("calificacion_riesgo_mercado"),
                    costos.get("comision_administracion_anual"),
                    costos.get("comision_desempeno"),
                    costos.get("gastos_totales_ter"),
                ))

                # 6. Rendimientos Historicos (por periodo con benchmark)
                rend = data.get("rendimientos_historicos", {})
                fecha_corte = rend.get("fecha_corte")
                periodos = rend.get("periodos", {})
                benchmarks = rend.get("benchmark", {})

                for periodo_key in ["1_mes", "3_meses", "12_meses", "3_anios"]:
                    valor_rend = periodos.get(periodo_key)
                    valor_bench = benchmarks.get(periodo_key)

                    # Solo insertar si al menos uno de los valores existe
                    if valor_rend is not None or valor_bench is not None:
                        try:
                            cursor.execute('''
                                INSERT INTO rendimientos_historicos (
                                    documento_id, periodo,
                                    valor_rendimiento, valor_benchmark,
                                    fecha_corte
                                ) VALUES (?, ?, ?, ?, ?)
                            ''', (
                                doc_id, periodo_key,
                                valor_rend, valor_bench,
                                fecha_corte,
                            ))
                        except sqlite3.IntegrityError:
                            logger.debug(
                                f"Rendimiento duplicado para doc={doc_id}, "
                                f"periodo={periodo_key}")

                conn.commit()
                logger.info(
                    f"Datos del fondo {clave} (Serie {serie_acc}) guardados en DB. "
                    f"TER={costos.get('gastos_totales_ter', 'N/D')}")
                return True

        except Exception as e:
            logger.error(f"Error al guardar en DB: {e}", exc_info=True)
            return False

    # -------------------------------------------------------------------
    # CONSULTAS ANALITICAS PARA DASHBOARD
    # -------------------------------------------------------------------
    def get_performance_vs_cost_data(self) -> List[Dict[str, Any]]:
        """Obtiene datos para el scatter plot TER vs Rendimiento 12 meses.

        Retorna una lista de diccionarios con:
            - clave_pizarra, serie_accionaria, categoria, institucion
            - gastos_totales_ter (numerico)
            - rendimiento_12m (numerico)
            - benchmark_12m (numerico)
            - tipo_administracion
        """
        query = '''
            SELECT
                f.clave_pizarra,
                s.serie_accionaria,
                f.categoria,
                i.nombre AS institucion,
                m.gastos_totales_ter,
                m.comision_administracion_anual,
                m.comision_desempeno,
                m.tipo_administracion,
                m.benchmark_oficial,
                r.valor_rendimiento AS rendimiento_12m,
                r.valor_benchmark AS benchmark_12m,
                r.fecha_corte
            FROM metricas_prospecto m
            JOIN documentos d ON m.documento_id = d.id
            JOIN series s ON d.serie_id = s.id
            JOIN fondos f ON s.fondo_id = f.id
            JOIN instituciones i ON f.institucion_id = i.id
            LEFT JOIN rendimientos_historicos r
                ON r.documento_id = d.id AND r.periodo = '12_meses'
            WHERE m.gastos_totales_ter IS NOT NULL
               OR r.valor_rendimiento IS NOT NULL
            ORDER BY f.clave_pizarra, s.serie_accionaria
        '''
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_all_rendimientos(self) -> List[Dict[str, Any]]:
        """Obtiene todos los rendimientos historicos para analisis."""
        query = '''
            SELECT
                f.clave_pizarra,
                s.serie_accionaria,
                f.categoria,
                i.nombre AS institucion,
                r.periodo,
                r.valor_rendimiento,
                r.valor_benchmark,
                r.fecha_corte
            FROM rendimientos_historicos r
            JOIN documentos d ON r.documento_id = d.id
            JOIN series s ON d.serie_id = s.id
            JOIN fondos f ON s.fondo_id = f.id
            JOIN instituciones i ON f.institucion_id = i.id
            ORDER BY f.clave_pizarra, r.periodo
        '''
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_comisiones_summary(self) -> List[Dict[str, Any]]:
        """Obtiene resumen de comisiones de todos los fondos."""
        query = '''
            SELECT
                f.clave_pizarra,
                s.serie_accionaria,
                f.categoria,
                i.nombre AS institucion,
                m.comision_administracion_anual,
                m.comision_desempeno,
                m.gastos_totales_ter,
                m.tipo_administracion,
                m.var_maximo_autorizado
            FROM metricas_prospecto m
            JOIN documentos d ON m.documento_id = d.id
            JOIN series s ON d.serie_id = s.id
            JOIN fondos f ON s.fondo_id = f.id
            JOIN instituciones i ON f.institucion_id = i.id
            ORDER BY m.gastos_totales_ter DESC
        '''
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_database_summary(self) -> Dict[str, int]:
        """Retorna conteos generales de la base de datos."""
        counts = {}
        tables = [
            "instituciones", "fondos", "series",
            "documentos", "metricas_prospecto", "rendimientos_historicos"
        ]
        with self._get_connection() as conn:
            for table in tables:
                try:
                    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = cursor.fetchone()[0]
                except sqlite3.OperationalError:
                    counts[table] = 0
        return counts
