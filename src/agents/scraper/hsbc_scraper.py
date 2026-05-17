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
from config.selectors import HSBCSelectors

logger = logging.getLogger(__name__)

class HSBCScraper:
    """
    Agente Scraper especializado para el portal de Fondos de HSBC México.
    Descarga el 'Documento Clave de Información' (DICI) y el 'Prospecto' para cada fondo.
    """
    
    def __init__(self, url: str, download_dir: str, manifest_path: str):
        """
        Inicializa el scraper de HSBC.
        
        Args:
            url: URL de la página de precios y rendimientos de HSBC.
            download_dir: Directorio base para descargas.
            manifest_path: Ruta al archivo CSV de seguimiento.
        """
        self.url = url
        self.download_dir = download_dir
        self.manifest_path = manifest_path
        self.retry_strategy = RetryStrategy(max_retries=3, backoff_factor=2.0)
        
        # Asegurar infraestructura de datos
        os.makedirs(self.download_dir, exist_ok=True)
        self._init_manifest()

    def _init_manifest(self) -> None:
        """Inicializa el archivo manifest si no existe."""
        if not os.path.exists(self.manifest_path):
            columns = [
                'fecha_consulta', 'fondo', 'tipo_documento', 'archivo_destino', 'estado'
            ]
            pd.DataFrame(columns=columns).to_csv(self.manifest_path, index=False)
            logger.info("Manifest de HSBC creado para trazabilidad.")

    def _navegar(self, page: Page) -> None:
        """Navega a la página y espera a que el dropdown esté listo."""
        logger.info(f"Navegando a HSBC: {self.url}")
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_selector(HSBCSelectors.DROPDOWN_FONDOS, state="visible", timeout=60000)

    def _limpiar_nombre(self, texto: str, max_len: int = 100) -> str:
        """Sanitiza strings para su uso como nombres de archivos."""
        texto_limpio = re.sub(r'[\\/*?:"<>|]', "", texto).strip()
        return texto_limpio[:max_len]

    def _descargar_documento(self, page: Page, selector: str, fondo_nombre: str, tipo_doc: str) -> Optional[str]:
        """Lógica genérica para descargar un documento desde un enlace en la fila."""
        try:
            # Preparar rutas
            fondo_clean = self._limpiar_nombre(fondo_nombre)
            directorio_fondo = os.path.join(self.download_dir, "HSBC", fondo_clean)
            os.makedirs(directorio_fondo, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d")
            nombre_archivo = f"{fondo_clean}_{tipo_doc}_{timestamp}.pdf"
            ruta_destino = os.path.join(directorio_fondo, nombre_archivo)

            # Si ya existe, saltar para ahorrar tiempo/ancho de banda
            if os.path.exists(ruta_destino):
                logger.debug(f"Documento ya existe: {nombre_archivo}")
                return ruta_destino

            # Descarga
            time.sleep(random.uniform(1.0, 2.5)) # Delay humano
            
            # El selector es específico para la fila actual, se pasa como locator
            link = page.locator(selector)
            if link.count() == 0:
                return None

            with page.expect_download(timeout=60000) as download_info:
                link.first.click()
            
            download = download_info.value
            download.save_as(ruta_destino)
            
            logger.info(f"Descargado ({tipo_doc}): {fondo_nombre} -> {nombre_archivo}")
            return ruta_destino

        except Exception as e:
            logger.error(f"Error descargando {tipo_doc} para {fondo_nombre}: {e}")
            return None

    def _procesar_familia(self, page: Page, familia_label: str) -> int:
        """Procesa todos los fondos de una familia seleccionada en el dropdown."""
        logger.info(f"--- Procesando familia de fondos: {familia_label} ---")
        
        # Seleccionar en el dropdown
        page.select_option(HSBCSelectors.DROPDOWN_FONDOS, label=familia_label)
        # Esperar a que la tabla se actualice (generalmente es rápido pero por seguridad)
        page.wait_for_timeout(2000)
        
        filas = page.locator(HSBCSelectors.FILAS_RESULTADOS)
        num_fondos = filas.count()
        logger.info(f"Encontrados {num_fondos} fondos en {familia_label}.")
        
        descargados_familia = 0
        manifest_records = []

        for i in range(num_fondos):
            fila = filas.nth(i)
            nombre_fondo = fila.locator(HSBCSelectors.COL_FONDO_NOMBRE).inner_text().strip()
            
            if not nombre_fondo:
                continue

            # 1. Descargar Prospecto
            ruta_prospecto = self._descargar_documento(
                page, 
                f"{HSBCSelectors.FILAS_RESULTADOS}:nth-child({i+1}) {HSBCSelectors.LINK_PROSPECTO}", 
                nombre_fondo, 
                "Prospecto"
            )
            if ruta_prospecto:
                descargados_familia += 1
                manifest_records.append({
                    'fecha_consulta': datetime.now().isoformat(),
                    'fondo': nombre_fondo,
                    'tipo_documento': 'Prospecto',
                    'archivo_destino': ruta_prospecto,
                    'estado': 'Exito'
                })

            # 2. Descargar DICI
            ruta_dici = self._descargar_documento(
                page, 
                f"{HSBCSelectors.FILAS_RESULTADOS}:nth-child({i+1}) {HSBCSelectors.LINK_DICI}", 
                nombre_fondo, 
                "DICI"
            )
            if ruta_dici:
                descargados_familia += 1
                manifest_records.append({
                    'fecha_consulta': datetime.now().isoformat(),
                    'fondo': nombre_fondo,
                    'tipo_documento': 'DICI',
                    'archivo_destino': ruta_dici,
                    'estado': 'Exito'
                })

        # Actualizar manifest
        if manifest_records:
            pd.DataFrame(manifest_records).to_csv(self.manifest_path, mode='a', header=False, index=False)
            
        return descargados_familia

    def extraer(self) -> None:
        """Método principal para orquestar la extracción de HSBC."""
        with sync_playwright() as p:
            # Evasión Antidetect para mantener consistencia y evitar timeouts
            browser = p.chromium.launch(
                headless=False,
                channel="msedge",
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                accept_downloads=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
            )
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            """)
            
            try:
                self.retry_strategy.execute(self._navegar, page)
                
                # Obtener todas las opciones del dropdown (excepto la primera si es placeholder)
                options = page.locator(f"{HSBCSelectors.DROPDOWN_FONDOS} option").all_inner_texts()
                logger.info(f"Familias de fondos encontradas: {len(options)}")
                
                total_descargados = 0
                for familia in options:
                    if familia.strip():
                        total_descargados += self._procesar_familia(page, familia)
                        # Delay entre familias
                        time.sleep(random.uniform(2.0, 4.0))
                
                logger.info(f"Extracción HSBC finalizada. Total descargados: {total_descargados}")
                
            except Exception as e:
                logger.critical(f"Fallo crítico en el scraper de HSBC: {e}")
            finally:
                browser.close()
