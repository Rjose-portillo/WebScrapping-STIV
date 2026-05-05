import os
import logging
import fitz  # PyMuPDF
from pdf2image import convert_from_path
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

class OCRAgent:
    """
    Agente encargado de detectar PDFs escaneados (imágenes) y transcribirlos
    mediante OCR (Tesseract) para facilitar su lectura por un LLM.
    """
    
    def __init__(self, tesseract_path: str = None):
        """
        Args:
            tesseract_path: Ruta al ejecutable de Tesseract si no está en el PATH.
        """
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path

    def is_pdf_image_only(self, pdf_path: str, threshold: float = 100) -> bool:
        """
        Verifica si un PDF es puramente imagen o tiene muy poco texto.
        
        Args:
            pdf_path: Ruta al archivo PDF.
            threshold: Número mínimo de caracteres para considerar que tiene texto.
        """
        try:
            doc = fitz.open(pdf_path)
            total_text = ""
            for page in doc:
                total_text += page.get_text()
            
            doc.close()
            return len(total_text.strip()) < threshold
        except Exception as e:
            logger.error(f"Error al verificar texto en {pdf_path}: {e}")
            return True

    def transcribe_pdf(self, pdf_path: str, output_path: str = None) -> str:
        """
        Convierte un PDF de imagen a texto plano usando OCR.
        
        Args:
            pdf_path: Ruta al PDF original.
            output_path: Ruta donde guardar el .txt. Si es None, usa el mismo nombre que el PDF.
        """
        if not output_path:
            output_path = pdf_path.rsplit('.', 1)[0] + "_transcribed.txt"

        logger.info(f"Iniciando transcripción OCR para: {pdf_path}")
        
        try:
            # Convertir páginas de PDF a imágenes
            # Poppler debe estar instalado para que esto funcione
            images = convert_from_path(pdf_path)
            
            full_text = []
            for i, image in enumerate(images):
                logger.info(f"Procesando página {i+1}/{len(images)}...")
                text = pytesseract.image_to_string(image, lang='spa') # 'spa' para español
                full_text.append(f"--- PÁGINA {i+1} ---\n{text}")
            
            transcribed_content = "\n\n".join(full_text)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(transcribed_content)
            
            logger.info(f"Transcripción completada con éxito: {output_path}")
            return transcribed_content
            
        except Exception as e:
            logger.error(f"Error durante el OCR de {pdf_path}: {e}")
            return ""

    def process_directory(self, root_dir: str):
        """
        Recorre un directorio y procesa todos los PDFs que sean solo imagen.
        """
        for root, dirs, files in os.walk(root_dir):
            for file in files:
                if file.lower().endswith('.pdf'):
                    pdf_path = os.path.join(root, file)
                    if self.is_pdf_image_only(pdf_path):
                        logger.info(f"PDF detectado como imagen: {file}. Iniciando OCR...")
                        self.transcribe_pdf(pdf_path)
                    else:
                        logger.info(f"PDF contiene texto nativo: {file}. No requiere OCR.")
