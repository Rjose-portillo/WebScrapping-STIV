import os
import logging
from dotenv import load_dotenv
from src.agents.scraper.stiv_scraper import STIVScraper
from src.agents.parser.pdf_parser import STIVParser

# 1. Configuración centralizada y gobernanza de logs
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/stiv_extraction.log", encoding='utf-8'),
        logging.StreamHandler() # Output a consola
    ]
)

logger = logging.getLogger(__name__)

def main():
    logger.info("====== INICIANDO PIPELINE DE DATOS STIV ======")
    
    # Cargar variables de entorno
    load_dotenv('config/.env')
    
    stiv_url = os.getenv('STIV_URL', 'https://stivconsultasexternas.cnbv.gob.mx/')
    download_dir = os.getenv('DOWNLOAD_DIR', 'data/raw')
    manifest_path = os.getenv('MANIFEST_PATH', 'data/raw/manifest.csv')
    
    # 2. Inicializar Agente Scraper
    scraper = STIVScraper(
        url=stiv_url, 
        download_dir=download_dir, 
        manifest_path=manifest_path
    )
    
    logger.info("Plan de ejecución: EXTRACCIÓN MASIVA (Paginación completa y agrupación por Entidad)")
    
    # Ejecutar la Extracción (recorrerá todas las páginas sin filtro)
    scraper.extraer()
    
    # 3. Fase Opcional: Readiness y Parsing para el LLM/RAG
    # logger.info("Iniciando preparación RAG...")
    # parser = STIVParser(raw_dir=download_dir, processed_dir='data/processed')
    # parser.parse_documents()
    
    logger.info("====== PIPELINE COMPLETADO ======")

if __name__ == "__main__":
    main()
