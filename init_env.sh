#!/bin/bash
# Configuración del entorno virtual para Linux/Mac o Git Bash en Windows

echo "Inicializando entorno virtual..."
python -m venv venv

# Activar el entorno virtual
source venv/Scripts/activate

echo "Actualizando pip..."
python -m pip install --upgrade pip

echo "Instalando dependencias..."
pip install -r requirements.txt

echo "Instalando navegadores de Playwright..."
playwright install chromium

echo "Entorno configurado exitosamente."
