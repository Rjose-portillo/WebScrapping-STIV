"""HSBC México fund document scraper with Azure WAF evasion.

Downloads DICIs and Prospectos from HSBC's price & yield portal.
Implements anti-detection patterns to avoid Azure WAF blocks.
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
from config.selectors import HSBCSelectors

logger = logging.getLogger(__name__)

# --- Anti-WAF configuration ---
# Rotate through realistic UA strings (Chrome/Edge latest stable)
_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
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

class HSBCScraper:
    """
    Agente Scraper especializado para el portal de Fondos de HSBC México.
    Descarga el 'Documento Clave de Información' (DICI) y el 'Prospecto' para cada fondo.
    """
    
    def __init__(self, url: str, download_dir: str, manifest_path: str) -> None:
        """
        Inicializa el scraper de HSBC.
        
        Args:
            url: URL de la página de precios y rendimientos de HSBC.
            download_dir: Directorio base para descargas.
            manifest_path: Ruta al archivo CSV de seguimiento.
        """
        self.url: str = url
        self.download_dir: str = download_dir
        self.manifest_path: str = manifest_path
        self.retry_strategy: RetryStrategy = RetryStrategy(max_retries=3, backoff_factor=2.0)
        
        # Asegurar infraestructura de datos
        os.makedirs(self.download_dir, exist_ok=True)
        self._init_manifest()

    def _init_manifest(self) -> None:
        """Inicializa el archivo manifest si no existe."""
        if not os.path.exists(self.manifest_path):
            columns: List[str] = [
                'fecha_consulta', 'fondo', 'tipo_documento', 'archivo_destino', 'estado'
            ]
            pd.DataFrame(columns=columns).to_csv(self.manifest_path, index=False)
            logger.info("Manifest de HSBC creado para trazabilidad.")

    def _navegar(self, page: Page) -> None:
        """Navega a la página y espera a que el dropdown esté listo."""
        logger.info(f"Navegando a HSBC: {self.url}")
        # Add random pre-navigation delay to avoid fingerprinting
        time.sleep(random.uniform(1.5, 3.0))
        page.goto(self.url, wait_until="networkidle", timeout=90000)
        # Extra stability wait for dynamic content
        page.wait_for_timeout(random.randint(1500, 3000))
        page.wait_for_selector(HSBCSelectors.DROPDOWN_FONDOS, state="visible", timeout=60000)
        logger.info("HSBC: Dropdown de fondos visible y listo.")

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

    def _create_stealth_context(self, browser: Browser) -> BrowserContext:
        """Create a browser context with comprehensive anti-detection."""
        selected_ua: str = random.choice(_USER_AGENTS)
        # Randomize viewport slightly to avoid fingerprinting
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
        logger.info(f"HSBC context: UA={selected_ua[:60]}..., viewport={width}x{height}")
        return context

    def _detect_waf_block(self, page: Page) -> bool:
        """Detect if Azure WAF or Cloudflare has blocked the request."""
        try:
            title: str = page.title().lower()
            body_start: str = page.locator("body").inner_text()[:300].lower()
            blocked_signals = ["403", "forbidden", "access denied", "blocked", "attention required"]
            return any(sig in title or sig in body_start for sig in blocked_signals)
        except Exception:
            return False

    def extraer(self) -> None:
        """Método principal para orquestar la extracción de HSBC."""
        with sync_playwright() as p:
            # Stealth browser launch with anti-detection args
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
                self.retry_strategy.execute(self._navegar, page)
                
                # WAF detection checkpoint
                if self._detect_waf_block(page):
                    raise RuntimeError(
                        "Azure WAF blocked HSBC request (403). "
                        "Cooldown period recommended before retry."
                    )
                
                # Obtener todas las opciones del dropdown (excepto la primera si es placeholder)
                options: List[str] = page.locator(
                    f"{HSBCSelectors.DROPDOWN_FONDOS} option"
                ).all_inner_texts()
                logger.info(f"Familias de fondos encontradas: {len(options)}")
                
                total_descargados: int = 0
                for idx, familia in enumerate(options):
                    if familia.strip():
                        total_descargados += self._procesar_familia(page, familia)
                        # Randomized delay between families (increasing with index)
                        base_delay: float = random.uniform(3.0, 6.0)
                        extra_delay: float = min(idx * 0.5, 5.0)  # progressive slowdown
                        time.sleep(base_delay + extra_delay)
                
                logger.info(f"Extracción HSBC finalizada. Total descargados: {total_descargados}")
                
            except Exception as e:
                logger.critical(f"Fallo crítico en el scraper de HSBC: {e}")
                raise
            finally:
                context.close()
                browser.close()
