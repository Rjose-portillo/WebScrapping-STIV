import sqlite3
import pandas as pd
import os

def inspect_database(db_path="data/tesis_prospectos.db"):
    if not os.path.exists(db_path):
        print(f"Error: La base de datos no existe en {db_path}")
        return

    conn = sqlite3.connect(db_path)
    
    print("\n=== RESUMEN DE LA BASE DE DATOS PARA TESIS ===")
    
    # 1. Conteo de instituciones
    query_inst = "SELECT nombre, tipo FROM instituciones"
    df_inst = pd.read_sql_query(query_inst, conn)
    print(f"\nInstituciones registradas ({len(df_inst)}):")
    print(df_inst)

    # 2. Conteo de fondos y documentos
    query_fondos = """
    SELECT i.nombre as Institucion, COUNT(f.id) as Total_Fondos, COUNT(d.id) as Total_Documentos
    FROM instituciones i
    JOIN fondos f ON i.id = f.institucion_id
    LEFT JOIN series s ON f.id = s.fondo_id
    LEFT JOIN documentos d ON s.id = d.serie_id
    GROUP BY i.nombre
    """
    df_fondos = pd.read_sql_query(query_fondos, conn)
    print("\nResumen de Fondos y Documentos por Institución:")
    print(df_fondos)

    # 3. Vista rápida de métricas para comparación
    query_metrics = """
    SELECT f.clave_pizarra, s.serie_accionaria, m.tipo_administracion, m.gastos_totales_ter, m.var_maximo_autorizado
    FROM metricas_prospecto m
    JOIN documentos d ON m.documento_id = d.id
    JOIN series s ON d.serie_id = s.id
    JOIN fondos f ON s.fondo_id = f.id
    LIMIT 10
    """
    df_metrics = pd.read_sql_query(query_metrics, conn)
    print("\nMuestra de Métricas Extraídas (Primeros 10):")
    print(df_metrics)

    conn.close()

if __name__ == "__main__":
    inspect_database()
