import sqlite3
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Gestor de base de datos para el análisis histórico de prospectos.
    Implementa validaciones de integridad, evita duplicados mediante hashes
    y organiza la información para análisis comparativo de tesis.
    """

    def __init__(self, db_path: str = "data/tesis_prospectos.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

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
                    hash_archivo TEXT UNIQUE NOT NULL, -- Validación anti-duplicados
                    ruta_archivo TEXT,
                    FOREIGN KEY (serie_id) REFERENCES series(id)
                )
            ''')

            # 5. Métricas Extraídas (Para comparación)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS metricas_prospecto (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    documento_id INTEGER UNIQUE,
                    tipo_administracion TEXT,
                    horizonte_inversion TEXT,
                    var_maximo_autorizado TEXT,
                    calificacion_riesgo_mercado INTEGER,
                    comision_administracion_anual TEXT,
                    gastos_totales_ter TEXT,
                    FOREIGN KEY (documento_id) REFERENCES documentos(id)
                )
            ''')

            # 6. Rendimientos Históricos
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rendimientos_historicos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    documento_id INTEGER,
                    periodo TEXT,
                    valor_rendimiento TEXT,
                    valor_benchmark TEXT,
                    fecha_corte TEXT,
                    FOREIGN KEY (documento_id) REFERENCES documentos(id)
                )
            ''')

            conn.commit()
            logger.info("Base de datos inicializada correctamente.")

    def save_extraction_result(self, data: Dict[str, Any], institution_name: str):
        """
        Guarda el resultado de una extracción validando la existencia de la institución,
        fondo y serie, y evitando duplicados por hash de archivo.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Asegurar Institución
                cursor.execute("INSERT OR IGNORE INTO instituciones (nombre) VALUES (?)", (institution_name,))
                cursor.execute("SELECT id FROM instituciones WHERE nombre = ?", (institution_name,))
                inst_id = cursor.fetchone()[0]

                # 2. Asegurar Fondo
                clave = data['fondo_serie']['clave_pizarra']
                cursor.execute('''
                    INSERT OR IGNORE INTO fondos (institucion_id, clave_pizarra, categoria)
                    VALUES (?, ?, ?)
                ''', (inst_id, clave, data['fondo_serie']['categoria']))
                cursor.execute("SELECT id FROM fondos WHERE clave_pizarra = ?", (clave,))
                fondo_id = cursor.fetchone()[0]

                # 3. Asegurar Serie
                serie_acc = data['fondo_serie']['serie_accionaria'] or "N/A"
                cursor.execute('''
                    INSERT OR IGNORE INTO series (fondo_id, serie_accionaria)
                    VALUES (?, ?)
                ''', (fondo_id, serie_acc))
                cursor.execute("SELECT id FROM series WHERE fondo_id = ? AND serie_accionaria = ?", (fondo_id, serie_acc))
                serie_id = cursor.fetchone()[0]

                # 4. Insertar Documento (Validación por hash)
                file_hash = data['metadata']['hash_archivo']
                try:
                    cursor.execute('''
                        INSERT INTO documentos (serie_id, tipo_documento, hash_archivo, ruta_archivo)
                        VALUES (?, ?, ?, ?)
                    ''', (serie_id, "Prospecto", file_hash, data['metadata']['nombre_archivo']))
                    doc_id = cursor.lastrowid
                except sqlite3.IntegrityError:
                    logger.warning(f"Documento duplicado saltado (Hash: {file_hash[:10]}...)")
                    return False

                # 5. Insertar Métricas
                cursor.execute('''
                    INSERT INTO metricas_prospecto (
                        documento_id, tipo_administracion, horizonte_inversion,
                        var_maximo_autorizado, calificacion_riesgo_mercado,
                        comision_administracion_anual, gastos_totales_ter
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    doc_id,
                    data['fondo_serie']['tipo_administracion'],
                    data['fondo_serie']['horizonte_inversion'],
                    data['metricas_riesgo']['var_maximo_autorizado'],
                    data['metricas_riesgo']['calificacion_riesgo_mercado'],
                    data['estructura_costos']['comision_administracion_anual'],
                    data['estructura_costos']['gastos_totales_ter']
                ))

                # 6. Rendimientos
                rend = data['rendimientos_historicos']
                fecha_corte = rend.get('fecha_corte')
                for periodo, valor in rend.get('periodos', {}).items():
                    if valor is not None:
                        cursor.execute('''
                            INSERT INTO rendimientos_historicos (documento_id, periodo, valor_rendimiento, valor_benchmark, fecha_corte)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (doc_id, periodo, valor, rend['benchmark'].get(periodo), fecha_corte))

                conn.commit()
                logger.info(f"Datos del fondo {clave} (Serie {serie_acc}) guardados en DB.")
                return True

        except Exception as e:
            logger.error(f"Error al guardar en DB: {e}")
            return False
