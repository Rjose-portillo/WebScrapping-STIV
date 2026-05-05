import os
import logging
import re
import time
import random
from datetime import datetime
from typing import List, Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeoutError

from src.agents.scraper.retry_strategy import RetryStrategy
from config.selectors import STIVSelectors

logger = logging.getLogger(__name__)

class STIVScraper:
    """
    Agente Scraper diseñado con principios de Data Governance y Data Readiness.
    Automatiza la navegación, filtrado y descarga de prospectos financieros.
    """
    
    def __init__(self, url: str, download_dir: str, manifest_path: str):
        """
        Inicializa el scraper con rutas de almacenamiento y configuración.
        
        Args:
            url: URL del portal STIV.
            download_dir: Directorio base para descargas.
            manifest_path: Ruta al archivo CSV de seguimiento.
        """
        self.url = url
        self.download_dir = download_dir
        self.manifest_path = manifest_path
        self.retry_strategy = RetryStrategy(max_retries=3, backoff_factor=2.0)
        
        # Data Governance: Asegurar infraestructura de datos (Archivos)
        os.makedirs(self.download_dir, exist_ok=True)
        
        self._init_manifest()

    def _init_manifest(self) -> None:
        """Inicializa el archivo manifest si no existe."""
        if not os.path.exists(self.manifest_path):
            columns = [
                'fecha_consulta', 'pizarra', 'tipo_documento', 'fecha_documento',
                'version', 'archivo_destino', 'estado'
            ]
            pd.DataFrame(columns=columns).to_csv(self.manifest_path, index=False)
            logger.info("Manifest creado para trazabilidad de descargas.")

    def _navegar_a_busqueda(self, page: Page) -> None:
        """Navega al portal principal y espera a que la tabla se cargue."""
        logger.info(f"Navegando al portal: {self.url}")
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible", timeout=60000)

    def _limpiar_nombre(self, texto: str, max_len: int = 100) -> str:
        """Sanitiza strings para su uso como nombres de archivos o carpetas."""
        texto_limpio = re.sub(r'[\\/*?:"<>|]', "", texto).strip()
        return texto_limpio[:max_len]

    def _procesar_resultados_pagina(self, page: Page) -> int:
        """
        Extrae la información de la página actual, descarga los binarios y actualiza el Manifest.
        
        Returns:
            int: Número de documentos descargados con éxito en la página.
        """
        filas = page.locator(STIVSelectors.FILAS_RESULTADOS)
        num_registros = filas.count()
        logger.info(f"Procesando {num_registros} registros en la página actual.")
        
        manifest_records = []
        descargados_en_pagina = 0
        
        for i in range(num_registros):
            fila = filas.nth(i)
            try:
                # 1. Extracción de Metadatos
                denominacion = fila.locator(STIVSelectors.COL_DENOMINACION).inner_text().strip()
                pizarra_actual = fila.locator(STIVSelectors.COL_PIZARRA).inner_text().strip()
                tipo_doc = fila.locator(STIVSelectors.COL_TIPO_DOC).inner_text().strip()
                fecha_doc = fila.locator(STIVSelectors.COL_FECHA).inner_text().strip()
                version = fila.locator(STIVSelectors.COL_VERSION).inner_text().strip()

                # 2. Validación: Solo "Prospecto"
                if "PROSPECTO" not in tipo_doc.upper():
                    logger.debug(f"Saltando registro {i+1}: Tipo '{tipo_doc}' no es Prospecto.")
                    continue
                
                # 3. Preparación de Rutas y Nombres (Data Readiness)
                denominacion_clean = self._limpiar_nombre(denominacion) or "Entidad_Desconocida"
                tipo_doc_clean = "Prospecto"
                fecha_clean = fecha_doc.replace("/", "-")
                version_clean = self._limpiar_nombre(version, 50).replace(" ", "_")
                
                directorio_entidad = os.path.join(self.download_dir, denominacion_clean)
                os.makedirs(directorio_entidad, exist_ok=True)
                
                nombre_archivo = f"{pizarra_actual}_{tipo_doc_clean}_{fecha_clean}_{version_clean}.pdf"
                ruta_destino = os.path.join(directorio_entidad, nombre_archivo)
                
                # 4. Descarga con Anti-Blocking Delay
                time.sleep(random.uniform(1.5, 3.5))

                with page.expect_download(timeout=60000) as download_info:
                    fila.locator(STIVSelectors.COL_ARCHIVO).click()
                
                download = download_info.value
                download.save_as(ruta_destino)
                
                logger.info(f"Descargado: {denominacion_clean} / {nombre_archivo}")
                estado = "Exito"
                descargados_en_pagina += 1

                # 5. Registro en Manifest
                manifest_records.append({
                    'fecha_consulta': datetime.now().isoformat(),
                    'pizarra': pizarra_actual,
                    'tipo_documento': tipo_doc_clean,
                    'fecha_documento': fecha_doc,
                    'version': version,
                    'archivo_destino': ruta_destino,
                    'estado': estado
                })

            except PlaywrightTimeoutError:
                logger.error(f"Timeout al descargar registro {i+1} en página.")
            except Exception as e:
                logger.error(f"Error inesperado en registro {i+1}: {e}")
                continue
            
        # Volcado Masivo al Manifest (Eficiencia)
        if manifest_records:
            df_nuevos = pd.DataFrame(manifest_records)
            df_nuevos.to_csv(self.manifest_path, mode='a', header=False, index=False)
            
        return descargados_en_pagina

    def extraer(self) -> None:
        """Controlador principal de extracción masiva con paginación y resiliencia."""
        with sync_playwright() as p:
            browser: Browser = p.chromium.launch(headless=False)
            context: BrowserContext = browser.new_context(accept_downloads=True)
            page: Page = context.new_page()
            
            try:
                self.retry_strategy.execute(self._navegar_a_busqueda, page)
                
                total_descargados = 0
                pagina_actual = 1
                
                while True:
                    logger.info(f"--- Procesando Página {pagina_actual} ---")
                    descargados = self._procesar_resultados_pagina(page)
                    total_descargados += descargados
                    
                    # Anti-Blocking: Delay entre cambios de página
                    time.sleep(random.uniform(4.0, 7.0))

                    # Manejo de Paginación
                    next_button = page.locator(STIVSelectors.BTN_SIGUIENTE).last
                    
                    if next_button.count() > 0 and "dxp-buttonDisabled" not in next_button.get_attribute("class"):
                        logger.info(f"Cambiando a página {pagina_actual + 1}...")
                        next_button.click()
                        
                        page.wait_for_timeout(3000) # Espera técnica para el postback
                        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible")
                        pagina_actual += 1
                    else:
                        logger.info("Fin de la base de datos alcanzado.")
                        break
                        
            except Exception as e:
                logger.critical(f"Fallo crítico en el pipeline de extracción: {e}")
                
            finally:
                logger.info(f"Extracción finalizada. Documentos totales: {total_descargados}")
                browser.close()

