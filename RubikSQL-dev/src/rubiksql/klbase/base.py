"""\
RubikSQL Knowledge Base (Refactored).

This module provides the refactored RubikSQLKLBase class with a clean architecture:
- Database-centric initialization (db_id only, database must be registered)
- Self-contained KB folder with kb_config.yaml snapshot
- Modular storage and engine initialization from config
"""

__all__ = [
    "RubikSQLKLBase",
]

from concurrent.futures import ThreadPoolExecutor, as_completed
from ahvn.utils.basic import (
    pj,
    load_yaml,
    save_yaml,
    touch_dir,
    exists_file,
)
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.klop import KLOp
from ahvn.ukf.base import BaseUKF
from ahvn.klbase import KLBase
from ahvn.klstore import DatabaseKLStore
from ahvn.klengine import ScanKLEngine, FacetKLEngine, VectorKLEngine, DAACKLEngine, MongoKLEngine
from ahvn.utils.basic.config_utils import HEAVEN_CM

from rubiksql.utils.config_utils import RUBIK_CM, rpj
from rubiksql.db.manager import RUBIK_DBM
from rubiksql.db.info import DatabaseInfo
from rubiksql.ukfs.col_ukft import ColumnUKFT

from .utils import build_condition, build_encoder, infer_engine_type

logger = get_logger(__name__)

from typing import Any, Dict, Optional, List, Set
from copy import deepcopy


class RubikSQLKLBase(KLBase):
    """\
    Refactored RubikSQL Knowledge Base.

    Clean initialization and storage/engine management:
    - Initialized with db_id only (database must be registered in RUBIK_DBM)
    - Creates self-contained kb/ folder with kb_config.yaml snapshot
    - Builds storages and engines from kb_config.yaml

    Args:
        db_id: Database identifier (must be registered in RUBIK_DBM)

    Raises:
        ValueError: If db_id is not registered in RUBIK_DBM

    Example:
        >>> kb = RubikSQLKLBase(db_id="mydb")
        >>> # KB is ready to use with storages and engines configured
    """

    def __init__(self, db_id: str):
        if not RUBIK_DBM.db_exists(db_id):
            raise ValueError(f"Database '{db_id}' not registered in RUBIK_DBM")
        super().__init__(name=db_id)
        self.db_id = db_id
        self.init()
        self.build_storages()
        self.build_engines()
        logger.debug(f"RubikSQLKLBase initialized for db_id='{db_id}' at '{self.kb_path}'")

    def init(self):
        """\
        Create and return KB folder path for database.
        """
        db_dir = RUBIK_DBM._get_db_dir(self.db_id)
        self.kb_path = pj(db_dir, "kb", abs=True)
        touch_dir(self.kb_path)
        self.kb_cache_path = pj(self.kb_path, ".cache")
        touch_dir(self.kb_cache_path)
        self.kb_config_path = pj(self.kb_path, "kb_config.yaml")
        if not exists_file(self.kb_config_path):
            save_yaml({"klbase": RUBIK_CM.get("klbase", dict())}, self.kb_config_path)
            logger.debug(f"Created kb_config.yaml at '{self.kb_config_path}'")
        else:
            logger.debug(f"Using existing kb_config from '{self.kb_config_path}'")
        self.config = load_yaml(self.kb_config_path).get("klbase", dict())

    def build_storages(self) -> None:
        """\
        Build storage backends from configuration.

        Storage config format:
            provider: Storage provider (sqlite, duckdb, postgresql, etc.)
            database: Database name/path (supports {db_id} placeholder)
            path_suffix: Optional path suffix relative to kb_path
            condition: Optional filter condition (type_include/type_exclude/tags)
        """
        for storage_name, storage_cfg in deepcopy(self.config.get("storages", {})).items():
            for key in list(storage_cfg.keys()):
                if key.endswith("_suffix"):
                    base_key = key[:-7]
                    storage_cfg[base_key] = pj(self.kb_path, storage_cfg.pop(key))
            for key in list(storage_cfg.keys()):
                if "{db_id}" in str(storage_cfg[key]):
                    storage_cfg[key] = storage_cfg[key].replace("{db_id}", self.db_id)

            if ("database" in storage_cfg) and isinstance(storage_cfg["database"], str):
                if storage_cfg.get("provider") in ["sqlite", "duckdb"]:
                    storage_cfg["database"] = pj(self.kb_path, storage_cfg["database"])
                else:
                    storage_cfg["database"] = storage_cfg["database"]
            elif "database" not in storage_cfg:
                if storage_cfg.get("provider") in ["sqlite", "duckdb"]:
                    storage_cfg["database"] = pj(self.kb_path, f"{self.db_id}_{storage_name}.{'db' if storage_cfg.get('provider') == 'sqlite' else 'duckdb'}")
                else:
                    storage_cfg["database"] = f"{self.db_id}_{storage_name}"
            condition_cfg = storage_cfg.pop("condition", None)
            if condition_cfg is not None:
                condition = build_condition(condition_cfg)
                if condition is not None:
                    storage_cfg["condition"] = condition

            try:
                storage = DatabaseKLStore(name=storage_name, **storage_cfg)
                self.add_storage(storage)
                logger.debug(f"Added storage '{storage_name}' (provider={storage_cfg.get('provider')})")
            except Exception as e:
                logger.error(f"Failed to create storage '{storage_name}': {e}")

    def build_engines(self) -> None:
        """\
        Build search engines from configuration.

        Args:
            engines_cfg: Dict mapping engine_name to engine_config

        Engine config format:
            type: Engine type (facet, daac, vector) - auto-inferred if not specified
            storage: Name of storage backend to use
            path_suffix/uri_suffix: Optional path suffix relative to kb_path
            condition: Optional filter condition (type_include/type_exclude)
            encoder: Optional encoder lambdas for query normalization
            desync: Whether to defer syncing (build DAAC later)
            inplace: Whether to build in-place (for facet engines)
            normalizer: Whether to normalize tokens (for DAAC engines)
            embedder: Embedder name for vector engines
        """
        for engine_name, engine_cfg in deepcopy(self.config.get("engines", {})).items():
            engine_type = engine_cfg.pop("type", None)
            storage_name = engine_cfg.pop("storage", "main")
            desync = engine_cfg.pop("desync", False)

            for key in list(engine_cfg.keys()):
                if key.endswith("_suffix"):
                    base_key = key[:-7]
                    engine_cfg[base_key] = pj(self.kb_path, engine_cfg.pop(key))
            for key in list(engine_cfg.keys()):
                if "{db_id}" in str(engine_cfg[key]):
                    engine_cfg[key] = engine_cfg[key].replace("{db_id}", self.db_id)

            if engine_type is None:
                engine_type = infer_engine_type(engine_cfg)
            storage = self.storages.get(storage_name)
            if storage is None:
                logger.warning(f"Storage '{storage_name}' not found for engine '{engine_name}', skipping.")
                continue
            condition_cfg = engine_cfg.pop("condition", None)
            if condition_cfg is not None:
                condition = build_condition(condition_cfg)
                if condition is not None:
                    engine_cfg["condition"] = condition
            encoder_cfg = engine_cfg.pop("encoder", None)
            if encoder_cfg is not None:
                encoder = build_encoder(encoder_cfg)
                if encoder is not None:
                    engine_cfg["encoder"] = encoder

            try:
                engine_types = {
                    "scan": ScanKLEngine,
                    "facet": FacetKLEngine,
                    "daac": DAACKLEngine,
                    "vector": VectorKLEngine,
                    "mongo": MongoKLEngine,
                }
                if engine_type not in engine_types:
                    raise ValueError(f"Unknown engine type '{engine_type}' for engine '{engine_name}'")
                engine = engine_types.get(engine_type)(name=engine_name, storage=storage, **engine_cfg)
                self.add_engine(engine, desync=desync)
                logger.debug(f"Added engine '{engine_name}' (type={engine_type}, storage={storage_name}, desync={desync})")
            except Exception as e:
                logger.error(f"Failed to create engine '{engine_name}': {e}")

    def get_db_info(self) -> Optional[DatabaseInfo]:
        """\
        Load database info for this KB.

        Returns:
            DatabaseInfo if stats section exists, None otherwise
        """
        return RUBIK_DBM.load_db_info(self.db_id)

    def get_db_connection(self):
        """\
        Get a database connection for this KB.

        Returns:
            Database instance

        Raises:
            ValueError: If database not found (should not happen after __init__)
        """
        return RUBIK_DBM.connect(self.db_id)

    def clear_kls(
        self,
        tab_id: Optional[str] = None,
        col_id: Optional[str] = None,
        system: bool = False,
    ) -> int:
        """\
        Clear legacy knowledge from the knowledge base.

        Removes DatabaseUKFT, TableUKFT, ColumnUKFT, and EnumUKFT knowledge objects
        based on the hierarchical scope. Uses facet engine search to find objects
        and batch removes them.

        Args:
            tab_id: Optional table identifier to narrow scope to single table.
                col_id requires tab_id to be specified.
            col_id: Optional column identifier to narrow scope to single column.
                Requires tab_id to be specified.
            system: Whether to include system knowledge objects.

        Returns:
            Number of knowledge objects removed.

        Raises:
            ValueError: If col_id is specified without tab_id.

        Examples:
            >>> kb.clear_kls()  # Clear all database knowledge
            >>> kb.clear_kls(tab_id="users")  # Clear single table
            >>> kb.clear_kls(tab_id="users", col_id="name")  # Clear single column
        """
        if col_id is not None and tab_id is None:
            raise ValueError("col_id requires tab_id to be specified")
        ukf_types = ["db-database", "db-table", "db-column", "db-enum"]
        tag_filters = {"DATABASE": self.db_id}
        if tab_id is not None:
            tag_filters["TABLE"] = tab_id
        if col_id is not None:
            tag_filters["COLUMN"] = col_id
        kids = [
            r.get("id")
            for r in self.search(
                engine="facet",
                type=ukf_types,
                owner=RUBIK_CM.get("user.user_id", "admin") if not system else list(set([RUBIK_CM.get("user.user_id", "admin"), "admin", "system"])),
                tags=KLOp.NF(**tag_filters),
                include=["id"],
            )
        ]
        self.batch_remove(kids)
        return len(kids)

    def get_entity(
        self,
        tab_id: Optional[str] = None,
        col_id: Optional[str] = None,
        enum_val: Optional[Any] = None,
        anchor: bool = False,
    ) -> Optional[BaseUKF]:
        """\
        Load a knowledge object for a specific database entity (db-database, db-table, db-column, db-enum).

        When tab_id is not specified, retrieves a single DatabaseUKFT object for the database.
        When tab_id is specified but col_id is not, retrieves a single TableUKFT object for the table.
        When both tab_id and col_id are specified, retrieves a single ColumnUKFT object for the column.
        Otherwise, retrieves the EnumUKFT object for the specified enum value.

        Args:
            tab_id: Optional table identifier.
            col_id: Optional column identifier.
            enum_val: Optional enum value (for enum knowledge).
            anchor: Whether to load the anchor UKF (for columns).

        Returns:
            Knowledge object if found, None otherwise.
        """
        if enum_val is not None and col_id is None:
            raise ValueError("enum_val requires col_id to be specified")
        if col_id is not None and tab_id is None:
            raise ValueError("col_id requires tab_id to be specified")

        type = "db-database" if tab_id is None else ("db-table" if col_id is None else ("db-column" if enum_val is None else "db-enum"))

        tag_filters = {"DATABASE": self.db_id}
        if tab_id is not None:
            tag_filters["TABLE"] = tab_id
        if col_id is not None:
            tag_filters["COLUMN"] = col_id
        if enum_val is not None:
            tag_filters["ENUM"] = enum_val

        kls = [
            r.get("kl")
            for r in self.search(
                engine="anchor" if anchor else "facet",
                type=type,
                inactive_mark=False,
                owner=list(set([RUBIK_CM.get("user.user_id", "admin"), "admin", "system"])),
                tags=KLOp.AND([KLOp.NF(slot=slot, value=value) for slot, value in tag_filters.items()]),
                orderby=["-priority"],
                include=["id", "kl"],
            )
            if r.get("kl") is not None
        ]

        if len(kls) == 0:
            return None

        # Prioritize user-owned knowledge over admin-owned or system knowledge
        has_user = any([k.owner == RUBIK_CM.get("user.user_id", "admin") for k in kls])
        if has_user:
            kls = [k for k in kls if k.owner == RUBIK_CM.get("user.user_id", "admin")]
        if len(kls) > 1:
            logger.warning(f"Multiple ({len(kls)}) knowledge objects found for entity (db_id={self.db_id}, tab_id={tab_id}, col_id={col_id}, ANCHOR={anchor}).")
        kl = kls[0]

        # Drop search metadata
        kl.metadata.pop("search")
        return kl

    def get_retrieval_config(self) -> Dict[str, Any]:
        """Get retrieval configuration for KL search."""
        return RUBIK_CM.get("klbase.retrieval", dict()) or {}

    def get_retrieval_mode(self) -> str:
        """Get default retrieval mode from config."""
        retrieval_cfg = self.get_retrieval_config()
        return retrieval_cfg.get("mode", "default")

    def _normalize_engine_spec(self, engine_spec: Any) -> Dict[str, Any]:
        """Normalize engine spec into a dict with name/mode/params."""
        if isinstance(engine_spec, str):
            return {"name": engine_spec}
        if isinstance(engine_spec, dict):
            return engine_spec
        return {}

    def _search_related_kls(
        self,
        results_by_id: Dict[int, Dict[str, Any]],
        search_depth: int,
    ) -> None:
        """Expand related KLs based on configured relation triggers.

        Args:
            results_by_id (Dict[int, Dict[str, Any]]): Level-0 kls retrieved based on user query.
            search_depth (int): The maximum related-kls search depth.
        """
        retrieval_cfg = self.get_retrieval_config()
        type_strategies = retrieval_cfg.get("type_strategies", {}) or {}

        rels_by_type: Dict[str, List[str]] = {}
        for kl_type, strategy in type_strategies.items():
            rels = list()
            rel = strategy.get("related-search-trigger", None)
            if rel is not None:
                if isinstance(rel, (list, tuple, set)):
                    rels.extend([str(r) for r in rel if r])
                else:
                    rels.append(str(rel))
            if rels:
                rels_by_type[kl_type] = rels

        if not rels_by_type:
            return

        storage = self.storages.get("main")
        if storage is None:
            return

        visited = set(results_by_id.keys())
        frontier = set(visited)
        for _ in range(search_depth):
            next_frontier = set()
            for kl_id in list(frontier):
                entry = results_by_id.get(kl_id)
                kl = entry.get("kl") if entry else None
                if kl is None:
                    continue
                rels = rels_by_type.get(kl.type, [])
                for rel in rels:
                    for obj_id in kl.obj_ids(rel=rel):
                        if obj_id in visited:
                            continue
                        related_kl = storage.get(obj_id, default=None)
                        if related_kl is None or not isinstance(related_kl, BaseUKF):
                            continue
                        related_kl.metadata |= {
                            "related-search": {
                                "source-kl": kl_id,
                                "rel": rel,
                            }
                        }
                        results_by_id[related_kl.id] = {"id": related_kl.id, "kl": related_kl}
                        visited.add(related_kl.id)
                        next_frontier.add(related_kl.id)
            if not next_frontier:
                break
            frontier = next_frontier

    def _get_related_search_config(self, retrieval_cfg: Dict[str, Any]) -> tuple[bool, int]:
        '''Get related-kl search configuration from retrieval config.
        '''
        related_search_cfg = retrieval_cfg.get("related_search", {}) or {}
        enabled_related_search = related_search_cfg.get("enabled", False)
        if enabled_related_search:
            max_rel_search_depth = related_search_cfg.get("max-depth", 3)
        else:
            max_rel_search_depth = 0
        return enabled_related_search, max_rel_search_depth

    def search_by_default_strategy(
        self,
        query: str,
        strategy_cfg: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search KLs using configured per-type strategies."""
        retrieval_cfg = self.get_retrieval_config()
        strategy_cfg = strategy_cfg or retrieval_cfg.get("type_strategies", {}) or {}
        enabled_related_search, max_rel_search_depth = self._get_related_search_config(retrieval_cfg)

        results_by_id: Dict[int, Dict[str, Any]] = {}
        grouped = dict()
        for kl_type, strategy in strategy_cfg.items():
            engines = (strategy or {}).get("engines", []) or []
            for engine_spec in engines: # search by kl type, every kl type search by its engines
                normalized = self._normalize_engine_spec(engine_spec)
                engine_name = normalized.get("name")
                if not engine_name or engine_name not in self.engines: # only accept engines configured with cur klbase
                    continue
                params = dict(normalized.get("params", {}) or {})
                if "whole_word" in params: # maybe configured in kl search engine config
                    lang = HEAVEN_CM.get("prompts.lang")
                    params["whole_word"] = (lang == "en")
                params = {**params, **kwargs}
                mode = normalized.get("mode")
                key = (engine_name, mode, repr(sorted(params.items())))
                grouped.setdefault(key, {"engine": engine_name, "mode": mode, "params": params, "types": set()})
                grouped[key]["types"].add(kl_type)

        def _run_group(group_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
            return self.search(
                engine=group_cfg["engine"],
                query=query,
                **group_cfg["params"],
            )

        # run multi engine search in parallel
        group_list = list(grouped.values())
        with ThreadPoolExecutor(max_workers=max(1, len(group_list))) as executor:
            future_search_res_map = {executor.submit(_run_group, group): group for group in group_list}
            for future in as_completed(future_search_res_map):
                engine_results = future.result()
                group = future_search_res_map[future]
                for r in engine_results:
                    kl = r.get("kl")
                    if kl is None or kl.type not in group["types"]: # only accept kls of the current kl type
                        continue
                    if kl.id not in results_by_id:
                        results_by_id[kl.id] = r | {"engine_name": group["engine"]}

        if enabled_related_search and max_rel_search_depth > 0:
            self._search_related_kls(results_by_id, max_rel_search_depth)

        return list(results_by_id.values())

    def search_kl(self, query: str, mode: Optional[str] = None, **kwargs) -> List[Dict[str, Any]]:
        """kl search by mode"""
        if mode  == "default":
            return self.search_by_default_strategy(query=query, **kwargs)
        else:
            return self.search_by_agent(query=query, **kwargs)

    def search_by_agent(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Agent-driven retrieval using KLBase engines as tools."""
        # todo
        from rubiksql.agents.retrieval_agent import RubikSQLRetrievalAgentSpec

        agent = RubikSQLRetrievalAgentSpec(klbase=self, **kwargs)
        results_by_id: Dict[int, Dict[str, Any]] = {}
        for chunk in agent.stream(query=query):
            continue
        return list(results_by_id.values())
