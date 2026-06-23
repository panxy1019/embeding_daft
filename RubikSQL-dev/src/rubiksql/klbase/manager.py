__all__ = [
    "KLBaseManager",
    "RUBIK_KBM",
]

"""\
KLBase Manager.

Singleton manager for RubikSQLKLBase instances to avoid redundant initialization.
Keyed by db_id for efficient reuse.
"""

import threading
from typing import Dict, Optional

from ahvn.utils.basic.log_utils import get_logger
from .base import RubikSQLKLBase

logger = get_logger(__name__)


class KLBaseManager:
    """\
    Singleton manager for RubikSQLKLBase instances.

    Thread-safe manager keyed by db_id to avoid redundant
    KLBase initialization, which can be expensive due to vector store loading.

    Simplified interface with only load() and purge() methods.
    """

    _instance: Optional["KLBaseManager"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> "KLBaseManager":
        """Ensure singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize the cache (only once)."""
        if not self._initialized:
            self._cache: Dict[str, RubikSQLKLBase] = {}
            self._lock = threading.Lock()
            self._initialized = True
            logger.info("KLBaseManager singleton initialized")

    def load(self, db_id: str) -> RubikSQLKLBase:
        """\
        Load or get cached KLBase instance for a database.

        Args:
            db_id: Database identifier (must be registered in RUBIK_DBM).

        Returns:
            RubikSQLKLBase instance (cached or newly created).
        """
        with self._lock:
            if db_id in self._cache:
                logger.debug(f"KLBase cache HIT for {db_id}")
                return self._cache[db_id]

            # Create new instance
            logger.debug(f"KLBase cache MISS for {db_id}, creating new instance")

            klbase = RubikSQLKLBase(db_id)
            self._cache[db_id] = klbase

            logger.debug(f"KLBase cached for {db_id} (total cached: {len(self._cache)})")
            return klbase

    def purge(self, db_id: Optional[str] = None) -> int:
        """\
        Purge KLBase instance(s) from cache.

        Args:
            db_id: Database identifier. If None, purges all cached instances.

        Returns:
            Number of instances purged.
        """
        with self._lock:
            if db_id is None:
                # Purge all
                count = len(self._cache)
                for key, klbase in self._cache.items():
                    if hasattr(klbase, "close"):
                        try:
                            klbase.close()
                        except Exception as e:
                            logger.warning(f"Error closing KLBase for {key}: {e}")
                self._cache.clear()
                logger.info(f"Purged all {count} KLBase instance(s)")
                return count
            else:
                # Purge single
                if db_id in self._cache:
                    klbase = self._cache.pop(db_id)
                    if hasattr(klbase, "close"):
                        try:
                            klbase.close()
                        except Exception as e:
                            logger.warning(f"Error closing KLBase for {db_id}: {e}")
                    logger.info(f"Purged KLBase instance {db_id}")
                    return 1
                return 0


# Global singleton instance
RUBIK_KBM = KLBaseManager()
