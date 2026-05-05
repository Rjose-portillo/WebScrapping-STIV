import time
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

class RetryStrategy:
    """
    Patrón Retry para manejar la intermitencia y caídas del servidor STIV.
    Garantiza una ejecución robusta ("Data Readiness") sin fallar en el primer timeout.
    """
    def __init__(self, max_retries: int = 3, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        retries = 0
        while retries <= self.max_retries:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning(f"Intento {retries + 1}/{self.max_retries + 1} falló en la función '{func.__name__}': {e}")
                if retries == self.max_retries:
                    logger.error(f"Se alcanzó el máximo de reintentos ({self.max_retries}). Fallo definitivo.")
                    raise e
                
                # Exponential backoff para no saturar al servidor
                sleep_time = self.backoff_factor ** retries
                logger.info(f"Esperando {sleep_time} segundos antes del próximo intento...")
                time.sleep(sleep_time)
                retries += 1
