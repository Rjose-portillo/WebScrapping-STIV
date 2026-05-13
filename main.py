import os
import logging
from dotenv import load_dotenv

from src.agents.scraper.stiv_scraper import STIVScraper
from src.agents.scraper.hsbc_scraper import HSBCScraper
from src.agents.parser.ocr_agent import OCRAgent
from src.agents.parser.cnbv_extractor import CNBVExtractor
from src.database.db_manager import DatabaseManager

# --- CONFIGURACIÓN DE LOGGING ---
os.makedirs('logs', exist_ok=True)
os.makedirs('data/processed', exist_ok=True)
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
    Orquestador principal del pipeline de datos STIV con enfoque en Tesis (Ingeniería Matemática).
    """
    logger.info("====== INICIANDO PIPELINE DE DATOS STIV (PROYECTO TESIS) ======")
    
    # Cargar variables de entorno
    load_dotenv('config/.env')
    
    stiv_url = os.getenv('STIV_URL', 'https://stivconsultasexternas.cnbv.gob.mx/ConsultaProspectoFondo.aspx')
    download_dir = os.getenv('DOWNLOAD_DIR', 'Archivos')
    manifest_path = os.getenv('MANIFEST_PATH', 'Archivos/manifest.csv')
    processed_dir = 'data/processed'
    
    # 1. FASE DE EXTRACCIÓN (Scraping)
    logger.info("Fase 1: Extracción / Descarga Masiva...")
    
    # 1.1 CNBV STIV
    logger.info("Iniciando extracción CNBV STIV...")
    stiv_scraper = STIVScraper(
        url=stiv_url, 
        download_dir=download_dir, 
        manifest_path=manifest_path
    )
    stiv_scraper.extraer()

    # 1.2 HSBC
    logger.info("Iniciando extracción HSBC...")
    hsbc_url = 'https://hsbctrading.hsbc.com.mx/investment/funds/price-yield'
    hsbc_manifest = os.path.join(download_dir, 'manifest_hsbc.csv')
    hsbc_scraper = HSBCScraper(
        url=hsbc_url,
        download_dir=download_dir,
        manifest_path=hsbc_manifest
    )
    hsbc_scraper.extraer()
    
    # 2. FASE DE PROCESAMIENTO OCR (Detección de escaneos)
    logger.info("Fase 2: Validación OCR / Data Readiness...")
    ocr_agent = OCRAgent()
    ocr_agent.process_directory(download_dir)
    
    # 3. FASE DE EXTRACCIÓN ESTRUCTURADA (Esquema de Tesis)
    logger.info("Fase 3: Extracción de Alta Precisión e Integración en DB...")
    extractor = CNBVExtractor()
    db_manager = DatabaseManager() # Se inicializa la base de datos
    
    # Recorrer archivos descargados (PDF o TXT generado por OCR)
    for root, _, files in os.walk(download_dir):
        for file in files:
            # Priorizamos el TXT si existe (significa que fue procesado por OCR)
            # De lo contrario procesamos el PDF original.
            file_path = os.path.join(root, file)
            
            # Evitar procesar archivos de metadatos o logs
            if file.lower().endswith(('.pdf', '.docx')):
                # Verificar si existe un transcrito OCR
                ocr_path = file_path.rsplit('.', 1)[0] + "_transcribed.txt"
                target_path = ocr_path if os.path.exists(ocr_path) else file_path
                
                try:
                    result = extractor.extract(target_path, url_stiv=stiv_url)
                    
                    # Determinar institución para la base de datos
                    institucion = "HSBC" if "HSBC" in root.upper() else "CNBV_STIV"
                    
                    # Guardar en Base de Datos (Validación de duplicados incluida)
                    db_manager.save_extraction_result(result, institution_name=institucion)
                    
                    # Guardar JSON como respaldo
                    json_name = f"{os.path.splitext(file)[0]}.json"
                    json_output = os.path.join(processed_dir, json_name)
                    extractor.save_json(result, json_output)
                    
                except Exception as e:
                    logger.error(f"Error procesando {file}: {e}")

    logger.info("====== PIPELINE COMPLETADO EXITOSAMENTE ======")

if __name__ == "__main__":
    main()
