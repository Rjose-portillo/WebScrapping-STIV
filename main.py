import os
import logging
from dotenv import load_dotenv

from src.agents.scraper.stiv_scraper import STIVScraper
from src.agents.parser.ocr_agent import OCRAgent

# --- CONFIGURACIÓN DE LOGGING ---
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/stiv_extraction.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def main():
    """
    Orquestador principal del pipeline de datos STIV.
    """
    logger.info("====== INICIANDO PIPELINE DE DATOS STIV ======")
    
    # Cargar variables de entorno
    load_dotenv('config/.env')
    
    stiv_url = os.getenv('STIV_URL', 'https://stivconsultasexternas.cnbv.gob.mx/ConsultaProspectoFondo.aspx')
    download_dir = os.getenv('DOWNLOAD_DIR', 'Archivos')
    manifest_path = os.getenv('MANIFEST_PATH', 'Archivos/manifest.csv')
    
    # 1. FASE DE EXTRACCIÓN (Scraping)
    logger.info("Iniciando Fase 1: Extracción Masiva...")
    scraper = STIVScraper(
        url=stiv_url, 
        download_dir=download_dir, 
        manifest_path=manifest_path
    )
    
    # Ejecutar la Extracción (recorrerá todas las páginas sin filtro)
    scraper.extraer()
    
    # 2. FASE DE PROCESAMIENTO OCR (Opcional)
    # Si detectamos que los PDFs son imágenes, los transcribimos para el LLM.
    logger.info("Iniciando Fase 2: Procesamiento OCR / Data Readiness...")
    ocr_agent = OCRAgent()
    ocr_agent.process_directory(download_dir)
    
    logger.info("====== PIPELINE COMPLETADO EXITOSAMENTE ======")

if __name__ == "__main__":
    main()
