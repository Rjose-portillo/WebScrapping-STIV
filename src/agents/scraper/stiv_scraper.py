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
        
        consecutive_errors = 0
        for i in range(num_registros):
            fila = filas.nth(i)
            try:
                # 1. Extracción de Metadatos
                denominacion = fila.locator(STIVSelectors.COL_DENOMINACION).inner_text().strip()
                pizarra_actual = fila.locator(STIVSelectors.COL_PIZARRA).inner_text().strip()
                tipo_doc = fila.locator(STIVSelectors.COL_TIPO_DOC).inner_text().strip()
                fecha_doc = fila.locator(STIVSelectors.COL_FECHA).inner_text().strip()
                version = fila.locator(STIVSelectors.COL_VERSION).inner_text().strip()

                # 2. Validación: "Prospecto" o "DICI" (Documento Clave)
                tipo_doc_upper = tipo_doc.upper()
                is_prospecto = "PROSPECTO" in tipo_doc_upper
                is_dici = "DICI" in tipo_doc_upper or "DOCUMENTO DE INFORMACIÓN CLAVE" in tipo_doc_upper or "INFORMACIÓN CLAVE" in tipo_doc_upper
                
                if not (is_prospecto or is_dici):
                    logger.debug(f"Saltando registro {i+1}: Tipo '{tipo_doc}' no es Prospecto ni DICI.")
                    continue
                
                # 3. Preparación de Rutas y Nombres (Data Readiness)
                denominacion_clean = self._limpiar_nombre(denominacion) or "Entidad_Desconocida"
                tipo_doc_clean = "Prospecto" if is_prospecto else "DICI"
                fecha_clean = fecha_doc.replace("/", "-")
                version_clean = self._limpiar_nombre(version, 50).replace(" ", "_")
                
                directorio_entidad = os.path.join(self.download_dir, denominacion_clean)
                os.makedirs(directorio_entidad, exist_ok=True)
                
                nombre_archivo = f"{pizarra_actual}_{tipo_doc_clean}_{fecha_clean}_{version_clean}.pdf"
                ruta_destino = os.path.join(directorio_entidad, nombre_archivo)
                
                # Si ya existe, saltar para ahorrar ancho de banda
                if os.path.exists(ruta_destino):
                    consecutive_errors = 0
                    continue
                
                # 4. Descarga con Anti-Blocking Delay (un poco más largo para evitar bloqueos)
                time.sleep(random.uniform(2.5, 4.5))

                with page.expect_download(timeout=60000) as download_info:
                    fila.locator(STIVSelectors.COL_ARCHIVO).click()
                
                download = download_info.value
                download.save_as(ruta_destino)
                
                logger.info(f"Descargado: {denominacion_clean} / {nombre_archivo}")
                estado = "Exito"
                descargados_en_pagina += 1
                consecutive_errors = 0 # Reiniciar contador al tener éxito

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
                consecutive_errors += 1
                logger.error(f"Timeout al descargar registro {i+1} en página (Falla {consecutive_errors}/3 consecutivas).")
                if consecutive_errors >= 3:
                    raise RuntimeError("Se detectaron 3 timeouts consecutivos de descarga. Es muy probable un bloqueo de sesión (403) o caída de servidor.")
            except Exception as e:
                logger.error(f"Error inesperado en registro {i+1}: {e}")
                continue
            
        # Volcado Masivo al Manifest (Eficiencia)
        if manifest_records:
            df_nuevos = pd.DataFrame(manifest_records)
            df_nuevos.to_csv(self.manifest_path, mode='a', header=False, index=False)
            
        return descargados_en_pagina

    def extraer(self) -> None:
        """Controlador principal de extracción masiva con paginación y evasión avanzada."""
        with sync_playwright() as p:
            # Evasión de Antidetect (WAF Azure)
            browser: Browser = p.chromium.launch(
                headless=False,
                channel="msedge",
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            context: BrowserContext = browser.new_context(
                accept_downloads=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
                viewport={"width": 1280, "height": 800}
            )
            
            page: Page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            """)
            
            try:
                self.retry_strategy.execute(self._navegar_a_busqueda, page)
                
                # Validar de inmediato si la IP está bloqueada por el WAF (403 Forbidden)
                page_title = page.title()
                if "403" in page_title or "Forbidden" in page_title or "403" in page.locator("body").inner_text()[:200]:
                    raise RuntimeError("WAF Bloqueó nuestra IP (403 Forbidden). Activando secuencia de autorrecuperación.")
                
                total_descargados = 0
                pagina_actual = 1
                paginas_totales = 1 # Valor por defecto
                
                # Obtener el total real de páginas parseando el texto del paginador
                try:
                    resumen_paginador = page.locator(".dxp-summary").first.inner_text()
                    # Ejemplo de texto: "Página 1 de 127 (6311 Documentos)"
                    match = re.search(r'de\s+(\d+)', resumen_paginador)
                    if match:
                        paginas_totales = int(match.group(1))
                        logger.info(f"Se detectaron {paginas_totales} páginas en total en STIV.")
                except Exception as e:
                    logger.warning(f"No se pudo determinar el total de páginas: {e}")
                
                while pagina_actual <= paginas_totales:
                    logger.info(f"--- Procesando Página {pagina_actual} de {paginas_totales} ---")
                    
                    # Evitar procesar la primera página si no es necesario (ya estamos ahí) o navegar
                    if pagina_actual > 1:
                        logger.info(f"Cambiando a la página {pagina_actual}...")
                        # PN0 es página 1, PN1 es página 2
                        # Ejecutar evento DevExpress
                        page.evaluate(f"aspxGVPagerOnClick('ctl00_DefaultPlaceholder_TablaDocumentos', 'PN{pagina_actual - 1}')")
                        
                        # Espera inteligente y robusta para páginas lentas (DevExpress postbacks)
                        page.wait_for_timeout(1000) # Breve pausa para asegurar que el loader aparezca
                        
                        # Esperar dinámicamente hasta 2 minutos a que el panel de carga desaparezca
                        try:
                            page.wait_for_selector(STIVSelectors.LOADING_PANEL, state="hidden", timeout=120000)
                        except Exception as e:
                            logger.warning(f"Timeout o problema al esperar el panel de carga: {e}")
                            
                        # Asegurarse de que la tabla esté visible de nuevo
                        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible", timeout=60000)
                        page.wait_for_timeout(random.uniform(2000, 4000)) # Espera biológica post-carga
                    
                    descargados = self._procesar_resultados_pagina(page)
                    total_descargados += descargados
                    
                    pagina_actual += 1
                        
            except Exception as e:
                logger.critical(f"Fallo crítico en el pipeline de extracción: {e}")
                raise e
                
            finally:
                logger.info(f"Extracción finalizada. Documentos totales: {total_descargados}")
                browser.close()

