import os
import logging
import pandas as pd
import time
import random
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from src.agents.scraper.retry_strategy import RetryStrategy
from config.selectors import STIVSelectors

logger = logging.getLogger(__name__)

class STIVScraper:
    """
    Agente Scraper diseñado con principios de Data Governance y Data Readiness.
    """
    def __init__(self, url: str, download_dir: str, manifest_path: str):
        self.url = url
        self.download_dir = download_dir
        self.manifest_path = manifest_path
        self.retry_strategy = RetryStrategy(max_retries=3, backoff_factor=2.0)
        
        # Data Governance: Asegurar infraestructura de datos (Archivos)
        os.makedirs(self.download_dir, exist_ok=True)
        
        # Inicializar el manifest (Catálogo de Metadatos) si no existe
        if not os.path.exists(self.manifest_path):
            pd.DataFrame(columns=[
                'fecha_consulta', 'pizarra', 'tipo_documento', 'fecha_documento',
                'version', 'archivo_destino', 'estado'
            ]).to_csv(self.manifest_path, index=False)
            logger.info("Manifest creado para trazabilidad de descargas.")

    def _navegar_a_busqueda(self, page):
        """Navega al portal principal y espera a que la tabla se cargue."""
        logger.info(f"Navegando al portal: {self.url}")
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible", timeout=60000)

    def _procesar_resultados_pagina(self, page) -> int:
        """Extrae la información de la página actual, descarga los binarios y actualiza el Manifest."""
        filas = page.locator(STIVSelectors.FILAS_RESULTADOS)
        num_registros = filas.count()
        logger.info(f"Procesando {num_registros} registros en la página actual.")
        
        manifest_records = []
        descargados_en_pagina = 0
        
        for i in range(num_registros):
            fila = filas.nth(i)
            try:
                # Extraer metadatos de la tabla
                denominacion = fila.locator(STIVSelectors.COL_DENOMINACION).inner_text().strip()
                pizarra_actual = fila.locator(STIVSelectors.COL_PIZARRA).inner_text().strip()
                tipo_doc = fila.locator(STIVSelectors.COL_TIPO_DOC).inner_text().strip()
                fecha_doc = fila.locator(STIVSelectors.COL_FECHA).inner_text().strip()
                version = fila.locator(STIVSelectors.COL_VERSION).inner_text().strip()

                # --- VALIDACIÓN DE FILTRO: Solo "Prospecto" ---
                if "PROSPECTO" not in tipo_doc.upper():
                    logger.info(f"Saltando registro {i+1}: Tipo '{tipo_doc}' no es Prospecto.")
                    continue
                
                # Limpieza de nombres para el FileSystem (Gobernanza)
                import re
                denominacion_clean = re.sub(r'[\\/*?:"<>|]', "", denominacion).strip()[:100]
                if not denominacion_clean:
                    denominacion_clean = "Entidad_Desconocida"
                    
                tipo_doc_clean = "Prospecto" # Ya validado
                fecha_clean = fecha_doc.replace("/", "-")
                version_clean = re.sub(r'[\\/*?:"<>|]', "", version).replace(" ", "_")[:50]
                
                # Estandarización de rutas: Archivos / Denominación / Pizarra_Tipo_Fecha_Version.pdf
                directorio_entidad = os.path.join(self.download_dir, denominacion_clean)
                os.makedirs(directorio_entidad, exist_ok=True)
                
                nombre_archivo = f"{pizarra_actual}_{tipo_doc_clean}_{fecha_clean}_{version_clean}.pdf"
                ruta_destino = os.path.join(directorio_entidad, nombre_archivo)
                
                # --- ANTI-BLOCKING: Delay aleatorio antes de descargar ---
                time.sleep(random.uniform(1.5, 3.5))

                # Orquestar Descarga
                with page.expect_download(timeout=60000) as download_info:
                    fila.locator(STIVSelectors.COL_ARCHIVO).click()
                download = download_info.value
                download.save_as(ruta_destino)
                
                logger.info(f"Descargado: {denominacion_clean} / {nombre_archivo}")
                estado = "Exito"
                descargados_en_pagina += 1

                # Registro de metadatos para el manifest
                manifest_records.append({
                    'fecha_consulta': datetime.now().isoformat(),
                    'pizarra': pizarra_actual,
                    'tipo_documento': tipo_doc_clean,
                    'fecha_documento': fecha_doc,
                    'version': version,
                    'archivo_destino': ruta_destino,
                    'estado': estado
                })

            except Exception as e:
                logger.error(f"Fallo al procesar registro {i+1}: {e}")
                # Loggear error en el manifest si es crítico
                continue
            
        # Volcar al Manifest
        if manifest_records:
            df_nuevos = pd.DataFrame(manifest_records)
            df_nuevos.to_csv(self.manifest_path, mode='a', header=False, index=False)
            
        return descargados_en_pagina

    def extraer(self):
        """Controlador principal de extracción masiva con paginación."""
        with sync_playwright() as p:
            # Iniciamos en modo visual (headless=False) para supervisar
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            
            try:
                # Navegamos al inicio
                self.retry_strategy.execute(self._navegar_a_busqueda, page)
                
                total_descargados = 0
                pagina_actual = 1
                
                while True:
                    logger.info(f"--- Procesando Página {pagina_actual} ---")
                    descargados = self._procesar_resultados_pagina(page)
                    total_descargados += descargados
                    
                    # --- ANTI-BLOCKING: Delay entre páginas ---
                    time.sleep(random.uniform(4.0, 7.0))

                    # Checar si existe botón de siguiente página y no está deshabilitado
                    next_button = page.locator(STIVSelectors.BTN_SIGUIENTE).last
                    
                    if next_button.count() > 0 and "dxp-buttonDisabled" not in next_button.get_attribute("class"):
                        logger.info("Avanzando a la siguiente página...")
                        next_button.click()
                        
                        # Esperar al PostBack de DevExpress
                        page.wait_for_timeout(3000) 
                        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible")
                        pagina_actual += 1
                    else:
                        logger.info("Se ha alcanzado la última página o no hay más resultados.")
                        break
                        
            except Exception as e:
                logger.error(f"El flujo de extracción masiva falló: {e}")
                
            finally:
                logger.info(f"Extracción finalizada. Total de documentos 'Prospecto' descargados: {total_descargados}")
                browser.close()

