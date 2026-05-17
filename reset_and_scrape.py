import os
import shutil
import logging
import random
import time
from datetime import datetime
import pandas as pd
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.agents.scraper.hsbc_scraper import HSBCScraper
from src.agents.scraper.stiv_scraper import STIVScraper
from config.selectors import STIVSelectors, HSBCSelectors
from src.agents.parser.cnbv_extractor import CNBVExtractor
from src.database.db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ResetAndScrape")

def clean_environment():
    """Wipes existing database and Archivos folder completely fresh since locks are released."""
    logger.info("--- Iniciando limpieza de entorno ---")
    
    # 1. Eliminar Base de Datos
    db_path = "data/tesis_prospectos.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            logger.info(f"Base de datos eliminada: {db_path}")
        except Exception as e:
            logger.error(f"No se pudo eliminar la BD: {e}")
            
    # 2. Vaciar carpeta Archivos
    download_dir = "Archivos"
    if os.path.exists(download_dir):
        try:
            shutil.rmtree(download_dir)
            logger.info(f"Directorio de descargas eliminado: {download_dir}")
        except Exception as e:
            logger.error(f"No se pudo limpiar la carpeta Archivos: {e}")
            
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    logger.info("Entorno limpiado con éxito.")

def scrape_stiv_random_10():
    """Scrapes exactly 10 random files from CNBV STIV with advanced stealth evasion to bypass 403 blocks."""
    logger.info("--- Iniciando Extracción Aleatoria de CNBV STIV con Evasión de Antidetect ---")
    stiv_url = 'https://stivconsultasexternas.cnbv.gob.mx/ConsultaProspectoFondo.aspx'
    download_dir = 'Archivos'
    manifest_path = os.path.join(download_dir, 'manifest.csv')
    
    # Re-inicializar manifest para STIV
    columns = [
        'fecha_consulta', 'pizarra', 'tipo_documento', 'fecha_documento',
        'version', 'archivo_destino', 'estado'
    ]
    pd.DataFrame(columns=columns).to_csv(manifest_path, index=False)
    
    scraper = STIVScraper(stiv_url, download_dir, manifest_path)
    descargados = []
    
    with sync_playwright() as p:
        # Usamos Microsoft Edge oficial instalado para máxima credibilidad en TLS y WAF
        browser = p.chromium.launch(
            headless=False,
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
            viewport={"width": 1280, "height": 800}
        )
        
        page = context.new_page()
        # Ocultar indicador webdriver
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            logger.info("Navegando a STIV CNBV con perfil evasivo...")
            page.goto(stiv_url, wait_until="networkidle")
            page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible", timeout=60000)
            
            # Hay 127 páginas en total
            paginas_totales = 127
            
            while len(descargados) < 10:
                # Seleccionar una página aleatoria
                rand_page = random.randint(1, paginas_totales)
                logger.info(f"Navegando a la página aleatoria: {rand_page} de {paginas_totales}")
                
                # Ejecutar evento DevExpress para cambiar página
                page.evaluate(f"aspxGVPagerOnClick('ctl00_DefaultPlaceholder_TablaDocumentos', 'PN{rand_page - 1}')")
                page.wait_for_timeout(random.uniform(4000, 6000)) # Demora simulando lectura humana
                page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible")
                
                # Extraer filas de la página actual
                filas = page.locator(STIVSelectors.FILAS_RESULTADOS)
                num_registros = filas.count()
                if num_registros == 0:
                    continue
                
                # Barajar filas para aleatoriedad completa en la página
                filas_indices = list(range(num_registros))
                random.shuffle(filas_indices)
                
                for idx in filas_indices:
                    if len(descargados) >= 10:
                        break
                        
                    fila = filas.nth(idx)
                    try:
                        denominacion = fila.locator(STIVSelectors.COL_DENOMINACION).inner_text().strip()
                        pizarra_actual = fila.locator(STIVSelectors.COL_PIZARRA).inner_text().strip()
                        tipo_doc = fila.locator(STIVSelectors.COL_TIPO_DOC).inner_text().strip()
                        fecha_doc = fila.locator(STIVSelectors.COL_FECHA).inner_text().strip()
                        version = fila.locator(STIVSelectors.COL_VERSION).inner_text().strip()
                        
                        # Validar tipo de documento
                        tipo_doc_upper = tipo_doc.upper()
                        is_prospecto = "PROSPECTO" in tipo_doc_upper
                        is_dici = "DICI" in tipo_doc_upper or "DOCUMENTO DE INFORMACIÓN CLAVE" in tipo_doc_upper or "INFORMACIÓN CLAVE" in tipo_doc_upper
                        
                        if not (is_prospecto or is_dici):
                            continue
                            
                        tipo_doc_clean = "Prospecto" if is_prospecto else "DICI"
                        denominacion_clean = scraper._limpiar_nombre(denominacion) or "Entidad_Desconocida"
                        fecha_clean = fecha_doc.replace("/", "-")
                        version_clean = scraper._limpiar_nombre(version, 50).replace(" ", "_")
                        
                        directorio_entidad = os.path.join(download_dir, denominacion_clean)
                        os.makedirs(directorio_entidad, exist_ok=True)
                        
                        nombre_archivo = f"{pizarra_actual}_{tipo_doc_clean}_{fecha_clean}_{version_clean}.pdf"
                        ruta_destino = os.path.join(directorio_entidad, nombre_archivo)
                        
                        if os.path.exists(ruta_destino):
                            continue
                            
                        # Delay aleatorio más largo para evitar comportamiento de bot repetitivo
                        time.sleep(random.uniform(2.5, 4.5))
                        
                        with page.expect_download(timeout=60000) as download_info:
                            fila.locator(STIVSelectors.COL_ARCHIVO).click()
                            
                        download = download_info.value
                        download.save_as(ruta_destino)
                        
                        logger.info(f"✅ STIV Descargado ({len(descargados)+1}/10): {nombre_archivo}")
                        descargados.append(ruta_destino)
                        
                        # Guardar en manifest
                        df_nuevo = pd.DataFrame([{
                            'fecha_consulta': datetime.now().isoformat(),
                            'pizarra': pizarra_actual,
                            'tipo_documento': tipo_doc_clean,
                            'fecha_documento': fecha_doc,
                            'version': version,
                            'archivo_destino': ruta_destino,
                            'estado': 'Exito'
                        }])
                        df_nuevo.to_csv(manifest_path, mode='a', header=False, index=False)
                        
                    except Exception as e:
                        logger.debug(f"Error procesando fila {idx}: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"Fallo en raspado STIV: {e}")
        finally:
            browser.close()
            
    return descargados

def scrape_hsbc_random_10():
    """Scrapes 10 random funds (Prospecto + DICI for each) from HSBC with stealth evasion."""
    logger.info("--- Iniciando Extracción Aleatoria de HSBC (10 fondos / 20 archivos) ---")
    hsbc_url = 'https://hsbctrading.hsbc.com.mx/investment/funds/price-yield'
    download_dir = 'Archivos'
    manifest_path = os.path.join(download_dir, 'manifest_hsbc.csv')
    
    # Re-inicializar manifest para HSBC
    columns = [
        'fecha_consulta', 'fondo', 'tipo_documento', 'archivo_destino', 'estado'
    ]
    pd.DataFrame(columns=columns).to_csv(manifest_path, index=False)
    
    scraper = HSBCScraper(hsbc_url, download_dir, manifest_path)
    fondos_descargados = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            scraper.retry_strategy.execute(scraper._navegar, page)
            
            options = page.locator(f"{HSBCSelectors.DROPDOWN_FONDOS} option").all_inner_texts()
            opciones_validas = [opt.strip() for opt in options if opt.strip()]
            
            logger.info("Analizando catálogo completo de fondos en HSBC...")
            catalogo_completo = []
            
            for familia in opciones_validas:
                page.select_option(HSBCSelectors.DROPDOWN_FONDOS, label=familia)
                page.wait_for_timeout(2000)
                
                filas = page.locator(HSBCSelectors.FILAS_RESULTADOS)
                num_fondos = filas.count()
                
                for i in range(num_fondos):
                    fila = filas.nth(i)
                    nombre_fondo = fila.locator(HSBCSelectors.COL_FONDO_NOMBRE).inner_text().strip()
                    if nombre_fondo:
                        catalogo_completo.append({
                            'familia': familia,
                            'index': i,
                            'nombre': nombre_fondo
                        })
                        
            logger.info(f"Catálogo HSBC cargado con {len(catalogo_completo)} fondos.")
            
            # Seleccionar 10 aleatorios
            seleccionados = random.sample(catalogo_completo, min(10, len(catalogo_completo)))
            logger.info(f"Seleccionados {len(seleccionados)} fondos al azar para descarga dual.")
            
            for idx, item in enumerate(seleccionados):
                familia = item['familia']
                i = item['index']
                nombre_fondo = item['nombre']
                
                page.select_option(HSBCSelectors.DROPDOWN_FONDOS, label=familia)
                page.wait_for_timeout(2000)
                
                logger.info(f"Descargando ({idx+1}/10) - Fondo: {nombre_fondo} (Familia: {familia})")
                
                # 1. Prospecto
                ruta_prospecto = scraper._descargar_documento(
                    page, 
                    f"{HSBCSelectors.FILAS_RESULTADOS}:nth-child({i+1}) {HSBCSelectors.LINK_PROSPECTO}", 
                    nombre_fondo, 
                    "Prospecto"
                )
                
                # 2. DICI
                ruta_dici = scraper._descargar_documento(
                    page, 
                    f"{HSBCSelectors.FILAS_RESULTADOS}:nth-child({i+1}) {HSBCSelectors.LINK_DICI}", 
                    nombre_fondo, 
                    "DICI"
                )
                
                if ruta_prospecto or ruta_dici:
                    fondos_descargados.append({
                        'nombre': nombre_fondo,
                        'prospecto': ruta_prospecto,
                        'dici': ruta_dici
                    })
                    
                    # Registrar en manifest
                    records = []
                    if ruta_prospecto:
                        records.append({'fecha_consulta': datetime.now().isoformat(), 'fondo': nombre_fondo, 'tipo_documento': 'Prospecto', 'archivo_destino': ruta_prospecto, 'estado': 'Exito'})
                    if ruta_dici:
                        records.append({'fecha_consulta': datetime.now().isoformat(), 'fondo': nombre_fondo, 'tipo_documento': 'DICI', 'archivo_destino': ruta_dici, 'estado': 'Exito'})
                    
                    if records:
                        pd.DataFrame(records).to_csv(manifest_path, mode='a', header=False, index=False)
                        
        except Exception as e:
            logger.error(f"Fallo en raspado HSBC: {e}")
        finally:
            browser.close()
            
    return fondos_descargados

def parse_and_populate_db():
    """Parses all downloaded PDFs and populates the clean DB."""
    logger.info("--- Iniciando Procesamiento Semántico e Inserción en DB ---")
    extractor = CNBVExtractor()
    db = DatabaseManager()
    
    total_procesados = 0
    total_exitos = 0
    
    download_dir = "Archivos"
    processed_dir = "data/processed"
    
    for root, _, files in os.walk(download_dir):
        for file in files:
            file_path = os.path.join(root, file)
            
            if file.lower().endswith('.pdf'):
                total_procesados += 1
                logger.info(f"Extrayendo datos de: {file_path}")
                
                try:
                    result = extractor.extract(file_path, url_stiv='https://stivconsultasexternas.cnbv.gob.mx/')
                    
                    # Si contiene error de documento vacio, lo ignoramos pero lo guardamos en JSON
                    if "error" in result:
                        logger.warning(f"Documento detectado como vacío (posible escaneo que requiere OCR): {file}")
                    
                    # Clasificar institución
                    institucion = "HSBC" if "HSBC" in root.upper() or "HSBC" in file.upper() else "CNBV_STIV"
                    
                    # Persistir en DB
                    if db.save_extraction_result(result, institution_name=institucion):
                        total_exitos += 1
                        logger.info(f"✅ Éxito al persistir en DB: {file}")
                    else:
                        logger.warning(f"⚠️ Saltado o error al guardar en la DB: {file}")
                        
                    # Guardar JSON
                    json_name = f"{os.path.splitext(file)[0]}.json"
                    extractor.save_json(result, os.path.join(processed_dir, json_name))
                    
                except Exception as e:
                    logger.error(f"Fallo crítico al extraer {file}: {e}")
                    
    logger.info(f"--- Proceso Finalizado ---")
    logger.info(f"Total archivos PDF encontrados: {total_procesados}")
    logger.info(f"Total persistidos con éxito: {total_exitos}")

def main():
    logger.info("====== INICIANDO PROCESO COMPLETO CON CONTROL DE EVASIÓN ======")
    clean_environment()
    
    # 1. Descargar STIV (10 aleatorios con evasión WAF)
    scrape_stiv_random_10()
    
    # 2. Descargar HSBC (10 fondos aleatorios con Prospecto + DICI)
    scrape_hsbc_random_10()
    
    # 3. Procesar semánticamente e insertar en DB
    parse_and_populate_db()
    
    logger.info("====== PROCESO COMPLETO CONCLUIDO CON ÉXITO ======")

if __name__ == '__main__':
    main()
