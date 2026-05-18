"""Retry strategy with exponential backoff and jitter for scraper resilience."""

import time
import random
import logging
from typing import Callable, Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryStrategy:
    """
    Exponential backoff retry with jitter for Azure WAF evasion.
    
    Ensures robust execution against intermittent server errors,
    rate-limiting, and transient WAF blocks without saturating the target.
    """

    def __init__(
        self,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        max_sleep: float = 120.0,
    ) -> None:
        """
        Args:
            max_retries: Maximum number of retry attempts.
            backoff_factor: Base multiplier for exponential delay.
            jitter: If True, add random jitter to prevent thundering herd.
            max_sleep: Cap for maximum sleep time between retries (seconds).
        """
        self.max_retries: int = max_retries
        self.backoff_factor: float = backoff_factor
        self.jitter: bool = jitter
        self.max_sleep: float = max_sleep

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute a callable with retry logic.
        
        Args:
            func: The function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            The return value of the function on success.
            
        Raises:
            The last exception if all retries are exhausted.
        """
        retries: int = 0
        last_exception: Exception = RuntimeError("No attempts made")
        
        while retries <= self.max_retries:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                logger.warning(
                    f"Intento {retries + 1}/{self.max_retries + 1} "
                    f"falló en '{func.__name__}': {type(e).__name__}: {e}"
                )
                if retries == self.max_retries:
                    logger.error(
                        f"Máximo de reintentos alcanzado ({self.max_retries}). "
                        f"Fallo definitivo en '{func.__name__}'."
                    )
                    raise last_exception
                
                # Exponential backoff with optional jitter
                base_sleep: float = self.backoff_factor ** retries
                if self.jitter:
                    sleep_time: float = base_sleep + random.uniform(0, base_sleep * 0.5)
                else:
                    sleep_time = base_sleep
                    
                sleep_time = min(sleep_time, self.max_sleep)
                logger.info(
                    f"Esperando {sleep_time:.1f}s antes del próximo intento "
                    f"(backoff={base_sleep:.1f}s, jitter={'on' if self.jitter else 'off'})..."
                )
                time.sleep(sleep_time)
                retries += 1
        
        # Should not reach here, but satisfy type checker
        raise last_exception
