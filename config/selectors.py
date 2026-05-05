# Selectores CSS/XPath para el portal STIV de la CNBV
# IMPORTANTE: Estos selectores son ilustrativos y deben ser ajustados inspeccionando
# el DOM real de la página de la CNBV, ya que las aplicaciones gubernamentales cambian de estructura.

class STIVSelectors:
    # Formulario de Búsqueda (Filtro dentro de la tabla DevExpress)
    INPUT_PIZARRA = "#ctl00_DefaultPlaceholder_TablaDocumentos_DXFREditorcol3_I" 
    # Para buscar, basta con presionar Enter después de escribir en el input
    
    # Tabla de Resultados
    TABLA_RESULTADOS = "#ctl00_DefaultPlaceholder_TablaDocumentos_DXMainTable" 
    FILAS_RESULTADOS = "tr[id^='ctl00_DefaultPlaceholder_TablaDocumentos_DXDataRow']"
    
    # Columnas de los datos (índices 1-based para nth-child)
    COL_DENOMINACION = "td:nth-child(3)" # Denominación Social Operadora
    COL_PIZARRA = "td:nth-child(4)"      # Clave del Fondo
    COL_FECHA = "td:nth-child(5)"        # Fecha de actualización
    COL_TIPO_DOC = "td:nth-child(6)"     # Columna que indica si es DICI o Prospecto
    COL_VERSION = "td:nth-child(7)"      # Versión del documento
    COL_ARCHIVO = "td:nth-child(9) a[id$='_Archivo']" # Enlace para descargar el PDF

    # Paginación
    BTN_SIGUIENTE = ".dxp-button[onclick*='PBN']"
    LOADING_PANEL = "#ctl00_DefaultPlaceholder_TablaDocumentos_TL"
