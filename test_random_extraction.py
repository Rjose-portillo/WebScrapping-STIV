import os
import logging
import random
import time
from datetime import datetime
import pandas as pd
from playwright.sync_api import sync_playwright

from src.agents.scraper.hsbc_scraper import HSBCScraper
from config.selectors import HSBCSelectors
from src.agents.parser.cnbv_extractor import CNBVExtractor
from src.database.db_manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def verify_10_random_hsbc():
    url = 'https://hsbctrading.hsbc.com.mx/investment/funds/price-yield'
    download_dir = 'Archivos'
    manifest_path = os.path.join(download_dir, 'manifest_hsbc_test.csv')
    
    scraper = HSBCScraper(url, download_dir, manifest_path)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        try:
            scraper.retry_strategy.execute(scraper._navegar, page)
            
            options = page.locator(f"{HSBCSelectors.DROPDOWN_FONDOS} option").all_inner_texts()
            opciones_validas = [opt.strip() for opt in options if opt.strip()]
            
            logger.info("Recopilando todos los fondos disponibles para seleccion aleatoria...")
            todos_los_fondos = []
            
            for familia in opciones_validas:
                page.select_option(HSBCSelectors.DROPDOWN_FONDOS, label=familia)
                page.wait_for_timeout(2000)
                
                filas = page.locator(HSBCSelectors.FILAS_RESULTADOS)
                num_fondos = filas.count()
                
                for i in range(num_fondos):
                    fila = filas.nth(i)
                    nombre_fondo = fila.locator(HSBCSelectors.COL_FONDO_NOMBRE).inner_text().strip()
                    if nombre_fondo:
                        todos_los_fondos.append({
                            'familia': familia,
                            'index': i,
                            'nombre': nombre_fondo
                        })
                        
            logger.info(f"Total de fondos encontrados: {len(todos_los_fondos)}")
            
            # Elegir 10 al azar
            seleccionados = random.sample(todos_los_fondos, min(10, len(todos_los_fondos)))
            logger.info(f"Se seleccionaron {len(seleccionados)} fondos aleatorios.")
            
            archivos_descargados = []
            
            # Descargar los 10 seleccionados
            for item in seleccionados:
                familia = item['familia']
                i = item['index']
                nombre_fondo = item['nombre']
                
                page.select_option(HSBCSelectors.DROPDOWN_FONDOS, label=familia)
                page.wait_for_timeout(2000)
                
                logger.info(f"Descargando Prospecto aleatorio: {nombre_fondo} (Familia: {familia})")
                ruta_prospecto = scraper._descargar_documento(
                    page, 
                    f"{HSBCSelectors.FILAS_RESULTADOS}:nth-child({i+1}) {HSBCSelectors.LINK_PROSPECTO}", 
                    nombre_fondo, 
                    "Prospecto"
                )
                
                if ruta_prospecto:
                    archivos_descargados.append(ruta_prospecto)
                    
            logger.info(f"Descargas completadas: {len(archivos_descargados)} archivos.")
            
            # Procesar con CNBVExtractor y DBManager
            extractor = CNBVExtractor()
            db = DatabaseManager()
            
            exitosos = 0
            for file_path in archivos_descargados:
                logger.info(f"Procesando extraccion de: {file_path}")
                try:
                    data = extractor.extract(file_path, url_stiv=url)
                    if db.save_extraction_result(data, "HSBC"):
                        exitosos += 1
                        logger.info(f"✅ Guardado en DB: {data.get('fondo_serie', {}).get('clave_pizarra')}")
                    else:
                        logger.warning("⚠️ No se pudo guardar (posible duplicado o error).")
                except Exception as e:
                    logger.error(f"Error procesando {file_path}: {e}")
                    
            logger.info(f"Verificacion finalizada. {exitosos} de {len(archivos_descargados)} guardados exitosamente en la DB.")
            
        except Exception as e:
            logger.error(f"Error en verificacion: {e}")
        finally:
            browser.close()

if __name__ == '__main__':
    verify_10_random_hsbc()
