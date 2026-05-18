"""STIV (CNBV) document scraper with Azure WAF evasion and DevExpress pagination.

Downloads Prospectos and DICIs from the CNBV document registry.
Implements anti-detection patterns against Azure Web Application Firewall.
"""

import os
import logging
import re
import time
import random
from datetime import datetime
from typing import List, Optional, Dict, Any

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

from src.agents.scraper.retry_strategy import RetryStrategy
from config.selectors import STIVSelectors

logger = logging.getLogger(__name__)

# --- Anti-WAF configuration ---
_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.2478.80",
]

_STEALTH_INIT_SCRIPT: str = """
    // Hide webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // Mimic Chrome runtime
    window.chrome = { runtime: {}, csi: function(){}, loadTimes: function(){} };
    // Realistic plugin array
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    // Fake language and platform
    Object.defineProperty(navigator, 'languages', {
        get: () => ['es-MX', 'es', 'en-US', 'en']
    });
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32'
    });
    // Disable permissions query for notifications
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
"""

# DevExpress pagination wait settings
_DXP_LOADING_VISIBLE_TIMEOUT_MS: int = 3000
_DXP_LOADING_HIDDEN_TIMEOUT_MS: int = 120000
_DXP_PAGE_CHANGE_POLL_ATTEMPTS: int = 40
_DXP_PAGE_CHANGE_POLL_INTERVAL_MS: int = 500

class STIVScraper:
    """
    Agente Scraper diseñado con principios de Data Governance y Data Readiness.
    Automatiza la navegación, filtrado y descarga de prospectos financieros.
    """
    
    def __init__(self, url: str, download_dir: str, manifest_path: str) -> None:
        """
        Inicializa el scraper con rutas de almacenamiento y configuración.
        
        Args:
            url: URL del portal STIV.
            download_dir: Directorio base para descargas.
            manifest_path: Ruta al archivo CSV de seguimiento.
        """
        self.url: str = url
        self.download_dir: str = download_dir
        self.manifest_path: str = manifest_path
        self.retry_strategy: RetryStrategy = RetryStrategy(max_retries=3, backoff_factor=2.0)
        
        # Data Governance: Asegurar infraestructura de datos (Archivos)
        os.makedirs(self.download_dir, exist_ok=True)
        
        self._init_manifest()

    def _init_manifest(self) -> None:
        """Inicializa el archivo manifest si no existe."""
        if not os.path.exists(self.manifest_path):
            columns: List[str] = [
                'fecha_consulta', 'pizarra', 'tipo_documento', 'fecha_documento',
                'version', 'archivo_destino', 'estado'
            ]
            pd.DataFrame(columns=columns).to_csv(self.manifest_path, index=False)
            logger.info("Manifest creado para trazabilidad de descargas.")

    def _navegar_a_busqueda(self, page: Page) -> None:
        """Navega al portal principal y espera a que la tabla se cargue."""
        logger.info(f"Navegando al portal: {self.url}")
        # Pre-navigation jitter to avoid rate-limit fingerprinting
        time.sleep(random.uniform(2.0, 4.0))
        page.goto(self.url, wait_until="networkidle", timeout=90000)
        # Extra wait for DevExpress JS initialization
        page.wait_for_timeout(random.randint(2000, 4000))
        page.wait_for_selector(STIVSelectors.TABLA_RESULTADOS, state="visible", timeout=60000)
        logger.info("STIV: Tabla de resultados visible y lista.")

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
        num_registros: int = filas.count()
        logger.info(f"Procesando {num_registros} registros en la página actual.")
        
        manifest_records: List[Dict[str, Any]] = []
        descargados_en_pagina: int = 0
        
        consecutive_errors: int = 0
        for i in range(num_registros):
            fila = filas.nth(i)
            try:
                # 1. Extracción de Metadatos
                denominacion: str = fila.locator(STIVSelectors.COL_DENOMINACION).inner_text().strip()
                pizarra_actual: str = fila.locator(STIVSelectors.COL_PIZARRA).inner_text().strip()
                tipo_doc: str = fila.locator(STIVSelectors.COL_TIPO_DOC).inner_text().strip()
                fecha_doc: str = fila.locator(STIVSelectors.COL_FECHA).inner_text().strip()
                version: str = fila.locator(STIVSelectors.COL_VERSION).inner_text().strip()

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
                
                # 4. Descarga con Anti-Blocking Delay (randomized with jitter)
                delay: float = random.uniform(3.0, 6.0) + random.expovariate(1.0)
                time.sleep(delay)

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

    def _create_stealth_context(self, browser: Browser) -> BrowserContext:
        """Create a browser context with comprehensive anti-detection."""
        selected_ua: str = random.choice(_USER_AGENTS)
        width: int = random.choice([1280, 1366, 1440, 1536])
        height: int = random.choice([768, 800, 900, 864])
        
        context: BrowserContext = browser.new_context(
            accept_downloads=True,
            user_agent=selected_ua,
            viewport={"width": width, "height": height},
            locale="es-MX",
            timezone_id="America/Mexico_City",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "es-MX,es;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
        )
        logger.info(f"STIV context: UA={selected_ua[:60]}..., viewport={width}x{height}")
        return context

    def _detect_waf_block(self, page: Page) -> bool:
        """Detect if Azure WAF has blocked the request."""
        try:
            title: str = page.title().lower()
            body_start: str = page.locator("body").inner_text()[:300].lower()
            blocked_signals = ["403", "forbidden", "access denied", "blocked"]
            return any(sig in title or sig in body_start for sig in blocked_signals)
        except Exception:
            return False

    def _wait_for_page_change(self, page: Page, target_page: int) -> bool:
        """Wait for DevExpress pagination to confirm page change via .dxp-summary text."""
        target_text: str = f"P\u00e1gina {target_page} de"
        for attempt in range(_DXP_PAGE_CHANGE_POLL_ATTEMPTS):
            try:
                summary_text: str = page.locator(".dxp-summary").first.inner_text()
                if target_text in summary_text:
                    logger.info(f"\u2713 Paginador confirm\u00f3: {summary_text}")
                    return True
            except Exception:
                pass
            page.wait_for_timeout(_DXP_PAGE_CHANGE_POLL_INTERVAL_MS)
        
        logger.warning(f"No se pudo verificar cambio a p\u00e1gina {target_page} tras {_DXP_PAGE_CHANGE_POLL_ATTEMPTS} intentos.")
        return False

    def extraer(self) -> None:
        """Controlador principal de extracción masiva con paginación y evasión avanzada."""
        with sync_playwright() as p:
            # Stealth browser launch
            browser: Browser = p.chromium.launch(
                headless=False,
                channel="msedge",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]
            )
            
            context: BrowserContext = self._create_stealth_context(browser)
            page: Page = context.new_page()
            page.add_init_script(_STEALTH_INIT_SCRIPT)
            
            try:
                self.retry_strategy.execute(self._navegar_a_busqueda, page)
                
                # WAF detection checkpoint
                if self._detect_waf_block(page):
                    raise RuntimeError(
                        "WAF Bloqueó nuestra IP (403 Forbidden). "
                        "Activando secuencia de autorrecuperación. Cooldown de 300s recomendado."
                    )
                
                total_descargados: int = 0
                pagina_actual: int = 1
                paginas_totales: int = 1
                
                # Obtener el total real de páginas parseando el texto del paginador
                try:
                    resumen_paginador: str = page.locator(".dxp-summary").first.inner_text()
                    # Ejemplo de texto: "Página 1 de 127 (6311 Documentos)"
                    match = re.search(r'de\s+(\d+)', resumen_paginador)
                    if match:
                        paginas_totales = int(match.group(1))
                        logger.info(f"Se detectaron {paginas_totales} páginas en total en STIV.")
                except Exception as e:
                    logger.warning(f"No se pudo determinar el total de páginas: {e}")
                
                while pagina_actual <= paginas_totales:
                    logger.info(f"--- Procesando Página {pagina_actual} de {paginas_totales} ---")
                    
                    # 1. Procesar registros de la página actual
                    descargados: int = self._procesar_resultados_pagina(page)
                    total_descargados += descargados
                    
                    # 2. Si ya llegamos a la última página, terminamos de inmediato
                    if pagina_actual >= paginas_totales:
                        logger.info("¡Se ha alcanzado y procesado la última página!")
                        break
                        
                    # 3. Cambiar a la siguiente página secuencialmente
                    siguiente_pagina: int = pagina_actual + 1
                    logger.info(f"Cambiando a la página {siguiente_pagina} usando el botón Siguiente...")
                    
                    btn_siguiente = page.locator(STIVSelectors.BTN_SIGUIENTE).first
                    if not btn_siguiente.is_visible():
                        # Fallback robusto en caso de que cambie el selector
                        btn_siguiente = page.locator(".dxp-button").filter(has=page.locator("img")).last
                        
                    # Click físico real en el botón de Siguiente
                    btn_siguiente.click()
                    
                    # Esperar la carga de la página de forma inteligente y segura
                    logger.info("Esperando que la página termine de cargar...")
                    
                    # A. Esperar a que el panel de carga aparezca y desaparezca (DevExpress postback)
                    try:
                        page.wait_for_selector(
                            STIVSelectors.LOADING_PANEL,
                            state="visible",
                            timeout=_DXP_LOADING_VISIBLE_TIMEOUT_MS,
                        )
                        logger.debug("Panel de carga detectado.")
                    except Exception:
                        pass
                        
                    try:
                        page.wait_for_selector(
                            STIVSelectors.LOADING_PANEL,
                            state="hidden",
                            timeout=_DXP_LOADING_HIDDEN_TIMEOUT_MS,
                        )
                        logger.debug("Panel de carga ocultado con éxito.")
                    except Exception:
                        pass
                        
                    # B. Confirm page change via .dxp-summary text
                    self._wait_for_page_change(page, siguiente_pagina)
                        
                    # C. Randomized biological pause post-load
                    bio_pause_ms: int = random.randint(2500, 5000)
                    page.wait_for_timeout(bio_pause_ms)
                    
                    # Avanzar el contador
                    pagina_actual = siguiente_pagina
                        
            except Exception as e:
                logger.critical(f"Fallo crítico en el pipeline de extracción: {e}")
                raise
                
            finally:
                logger.info(f"Extracción finalizada. Documentos totales: {total_descargados}")
                context.close()
                browser.close()

