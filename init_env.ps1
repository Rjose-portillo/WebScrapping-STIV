# Configuración del entorno virtual para PowerShell (Windows Nativos)

Write-Host "Inicializando entorno virtual..." -ForegroundColor Green
python -m venv venv

# Activar el entorno virtual
.\venv\Scripts\Activate.ps1

Write-Host "Actualizando pip..." -ForegroundColor Green
python -m pip install --upgrade pip

Write-Host "Instalando dependencias..." -ForegroundColor Green
pip install -r requirements.txt

Write-Host "Instalando navegadores de Playwright..." -ForegroundColor Green
playwright install chromium

Write-Host "NOTA: Para las funciones de OCR (reconocimiento de texto en imágenes), asegúrate de tener instalado Tesseract OCR en tu sistema." -ForegroundColor Yellow
Write-Host "Puedes descargarlo aquí: https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Cyan

Write-Host "Entorno configurado exitosamente. Ahora puedes ejecutar 'python main.py'" -ForegroundColor Cyan
