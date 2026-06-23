"""\
RubikSQL Knowledge Base.

This module provides the RubikSQLKLBase class for managing knowledge bases
with staged build processes and progress tracking.
"""

__all__ = [
    "RubikSQLKLBase",
]

import time
import threading
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple, Type

from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.path_utils import pj
from ahvn.utils.basic.file_utils import exists_file
from ahvn.utils.basic.serialize_utils import load_json, save_json
from ahvn.utils.basic.progress_utils import Progress, NoProgress
from ahvn.utils.basic.parallel_utils import Parallelized
from ahvn.utils.basic.config_utils import dget
from ahvn.klbase import KLBase

from ..ukfs import DatabaseUKFT, TableUKFT, ColumnUKFT, EnumUKFT
from ..utils.config_utils import RUBIK_CM
from ..utils.db_utils import ColumnType
from ..utils.progress_utils import RubikSQLRichProgress

from .utils import build_condition, build_encoder, infer_engine_type
from .stages__legacy import (
    BuildStage,
    STAGE_ORDER,
    list_stages,
    get_normalized_weights,
    get_accumulated_weights,
)

logger = get_logger(__name__)


class RubikSQLKLBase(KLBase):
    """\
    RubikSQL Knowledge Base with staged build support.

    Provides a comprehensive API for building knowledge bases with:
    - Individual stage builders (all generators with progress)
    - Composite build_stream for full builds
    - Stage selection and progress restoration
    """

    def __init__(self, db, db_info, path, db_id: str = None, **kwargs):
        super().__init__(**kwargs)
        self.path = path
        self.db = db
        self.info = db_info
        self.db_id = db_id or db_info.get("name", "default") if db_info else "default"

        from ahvn.klstore import DatabaseKLStore
        from ahvn.klengine import FacetKLEngine, VectorKLEngine, DAACKLEngine

        # Build storages from config
        storages_cfg = RUBIK_CM.get("klbase.storages", dict())
        for storage_name, storage_cfg in storages_cfg.items():
            storage_cfg = dict(storage_cfg)
            for key in list(storage_cfg.keys()):
                if key.endswith("_suffix"):
                    base_key = key[:-7]
                    storage_cfg[base_key] = pj(self.path, storage_cfg.pop(key))
            if "database" in storage_cfg and isinstance(storage_cfg["database"], str):
                formatted_db = storage_cfg["database"].format(db_id=self.db_id)
                if storage_cfg.get("provider") == "sqlite":
                    storage_cfg["database"] = pj(self.path, formatted_db)
                else:
                    storage_cfg["database"] = formatted_db
            elif "database" not in storage_cfg and storage_cfg.get("provider") == "sqlite":
                storage_cfg["database"] = pj(self.path, f"{storage_name}.db")
            self.add_storage(DatabaseKLStore(name=storage_name, **storage_cfg))

        # Build engines from config
        engines_cfg = RUBIK_CM.get("klbase.engines", dict())
        for engine_name, engine_cfg in engines_cfg.items():
            engine_cfg = dict(engine_cfg)
            engine_type = engine_cfg.pop("type", None)
            storage_name = engine_cfg.pop("storage", "main")
            desync = engine_cfg.pop("desync", False)

            for key in list(engine_cfg.keys()):
                if key.endswith("_suffix"):
                    base_key = key[:-7]
                    engine_cfg[base_key] = pj(self.path, engine_cfg.pop(key))

            condition_cfg = engine_cfg.pop("condition", None)
            condition = build_condition(condition_cfg)
            if condition is not None:
                engine_cfg["condition"] = condition

            encoder_cfg = engine_cfg.pop("encoder", None)
            if encoder_cfg is not None:
                engine_cfg["encoder"] = build_encoder(encoder_cfg)

            storage = self.storages.get(storage_name)
            if storage is None:
                logger.warning(f"Storage '{storage_name}' not found for engine '{engine_name}', skipping.")
                continue

            if engine_type is None:
                engine_type = infer_engine_type(engine_cfg)

            if engine_type == "facet":
                engine = FacetKLEngine(name=engine_name, storage=storage, **engine_cfg)
            elif engine_type == "daac":
                engine = DAACKLEngine(name=engine_name, storage=storage, **engine_cfg)
            elif engine_type == "vector":
                engine = VectorKLEngine(name=engine_name, storage=storage, **engine_cfg)
            else:
                logger.warning(f"Unknown engine type '{engine_type}' for engine '{engine_name}', skipping.")
                continue

            self.add_engine(engine, desync=desync)

    # =========================================================================
    # Public API: Stage Information
    # =========================================================================

    @staticmethod
    def list_build_stages() -> List[str]:
        """\
        List all build stage names in execution order.

        Returns:
            List of stage name strings.
        """
        return list_stages()

    # =========================================================================
    # Public API: Individual Stage Builders
    # =========================================================================

    def build_database_kl(
        self,
        db_id: str,
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, DatabaseUKFT]:
        """\
        Build the database KL.

        Args:
            db_id: Database identifier.
            progress: Optional progress tracker.

        Yields:
            Progress events with keys: progress, message, step, step_progress.

        Returns:
            The built DatabaseUKFT instance (via generator return).
        """
        db_kl = DatabaseUKFT.from_db(
            self.db,
            db_id=db_id,
            short_description=dget(self.info, f"db.db_info.{db_id}.short_description"),
            synonyms=set(dget(self.info, f"db.db_info.{db_id}.synonyms", list())),
        ).signed(system=True)

        yield {
            "stage": str(BuildStage.DATABASE),
            "step_progress": 1.0,
            "message": "kb.database",
        }

        return db_kl

    def build_table_kls(
        self,
        db_kl: DatabaseUKFT,
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, Dict[str, TableUKFT]]:
        """\
        Build table KLs.

        Args:
            db_kl: Parent database KL.
            progress: Optional progress tracker.

        Yields:
            Progress events.

        Returns:
            Dict mapping table_id to TableUKFT.
        """
        tabs_info = self.info.get("tabs_info", dict())
        total_tabs = len(tabs_info)

        if total_tabs == 0:
            return {}

        tab_kls = {}
        db_template = self.db

        def _build_tab(tab_id: str, tab_info: Dict[str, Any]) -> TableUKFT:
            logger.debug(f"[parallel][tables] building {tab_id} on {threading.current_thread().name}")
            db = db_template.clone()
            try:
                return TableUKFT.from_tab(
                    db=db,
                    db_id=db_kl.db_id,
                    tab_id=tab_id,
                    short_description=tab_info.get("short_description"),
                    synonyms=set(tab_info.get("synonyms", list())),
                )
            finally:
                db.close()

        args = [{"tab_id": tid, "tab_info": tinfo} for tid, tinfo in tabs_info.items()]
        pbar = progress or NoProgress(total=0, desc="Tables")
        pbar.reset(total=total_tabs)
        progress_cls = type(pbar)

        with Parallelized(_build_tab, args=args, num_threads=self._parallel_workers("tables"), desc="Tables", progress=progress_cls) as tasks:
            completed = 0
            for kwargs, tab_kl, error in tasks:
                if error:
                    tasks._handle_interrupt()
                    raise error
                tab_id = kwargs["tab_id"]
                signed_kl = tab_kl.signed(system=True)
                signed_kl.link(dir="object", rel="in_database", kl=db_kl)
                db_kl.link(dir="object", rel="has_table", kl=signed_kl)
                tab_kls[tab_id] = signed_kl
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.TABLES),
                    "step_progress": completed / total_tabs,
                    "step_current": completed,
                    "step_total": total_tabs,
                    "message": f"kb.buildingTable:{tab_id}",
                }

        return tab_kls

    def build_column_kls(
        self,
        db_kl: DatabaseUKFT,
        tab_kls: Dict[str, TableUKFT],
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, Tuple[Dict[str, ColumnUKFT], Dict[str, Dict[str, ColumnUKFT]]]]:
        """\
        Build column KLs.

        Args:
            db_kl: Parent database KL.
            tab_kls: Dict of table KLs.
            progress: Optional progress tracker.

        Yields:
            Progress events.

        Returns:
            Tuple of (col_kls dict, col_kls_by_tab dict).
        """
        cols_info = self.info.get("cols_info", dict())
        total_cols = len(cols_info)

        if total_cols == 0:
            return {}, {}

        col_kls = {}
        col_kls_by_tab = {}
        db_template = self.db

        def _build_col(col_name: str, col_info: Dict[str, Any], tab_kl: TableUKFT) -> Tuple[str, str, ColumnUKFT]:
            tab_id, col_id = (eval(x.strip()) for x in col_name.split(".", 1))
            logger.debug(f"[parallel][columns] building {tab_id}.{col_id} on {threading.current_thread().name}")
            db = db_template.clone()
            try:
                pks = tab_kl.get("pks", [])
                is_pk = any((bool((len(pk) == 1) and (col_id in pk)) for pk in pks))
                tab_fks = tab_kl.get("fks", [])
                col_fks = [{k: v for k, v in fk.items() if k != "col_name"} for fk in tab_fks if fk.get("col_name") == col_id]
                col_kl = ColumnUKFT.from_col(
                    db=db,
                    db_id=db_kl.db_id,
                    tab_id=tab_id,
                    col_id=col_id,
                    short_description=col_info.get("short_description"),
                    synonyms=set(col_info.get("synonyms", list())),
                    is_pk=is_pk,
                    fks=col_fks,
                )
                return tab_id, col_id, col_kl.type_deduced()
            finally:
                db.close()

        args = [
            {
                "col_name": col_name,
                "col_info": col_info,
                "tab_kl": tab_kls[eval(col_name.split(".", 1)[0].strip())],
            }
            for col_name, col_info in cols_info.items()
        ]

        pbar = progress or NoProgress(total=0, desc="Columns")
        pbar.reset(total=total_cols)
        progress_cls = type(pbar)

        with Parallelized(_build_col, args=args, num_threads=self._parallel_workers("columns"), desc="Columns", progress=progress_cls) as tasks:
            completed = 0
            for kwargs, result, error in tasks:
                if error:
                    tasks._handle_interrupt()
                    raise error
                tab_id, col_id, col_kl = result
                signed_kl = col_kl.signed(system=True)
                signed_kl.link(dir="object", rel="in_database", kl=db_kl)
                signed_kl.link(dir="object", rel="in_table", kl=tab_kls[tab_id])
                tab_kls[tab_id].link(dir="object", rel="has_column", kl=signed_kl)

                col_kls_by_tab.setdefault(tab_id, dict())
                col_kls_by_tab[tab_id][col_id] = signed_kl
                col_kls[col_kl.name] = signed_kl
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.COLUMNS),
                    "step_progress": completed / total_cols,
                    "step_current": completed,
                    "step_total": total_cols,
                    "message": f"kb.buildingColumn:{col_kl.name}",
                }

        return col_kls, col_kls_by_tab

    def build_enum_kls(
        self,
        db_kl: DatabaseUKFT,
        col_kls: Dict[str, ColumnUKFT],
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, List[EnumUKFT]]:
        """\
        Build enum KLs.

        Args:
            db_kl: Parent database KL.
            col_kls: Dict of column KLs.
            progress: Optional progress tracker.

        Yields:
            Progress events.

        Returns:
            List of EnumUKFT instances.
        """
        total_cols = len(col_kls)
        if total_cols == 0:
            return []

        enum_kls = []

        def _build_enum(col_name: str, col_kl: ColumnUKFT) -> List[EnumUKFT]:
            if col_kl.datatype in [
                ColumnType.DateTime,
                ColumnType.Float,
                ColumnType.Identifier,
                ColumnType.LongText,
            ]:
                return []
            tab_id, col_id = (eval(x.strip()) for x in col_name.split(".", 1))
            logger.debug(f"[parallel][enums] building enums for {tab_id}.{col_id} on {threading.current_thread().name}")
            built = []
            for enum_val in col_kl.enums:
                enum_kl = EnumUKFT.from_enum(
                    db_id=db_kl.db_id,
                    tab_id=tab_id,
                    col_id=col_id,
                    enum_val=enum_val,
                )
                built.append(enum_kl.signed(system=True))
            return built

        args = [{"col_name": cn, "col_kl": ckl} for cn, ckl in col_kls.items()]

        pbar = progress or NoProgress(total=0, desc="Enums")
        pbar.reset(total=total_cols)
        progress_cls = type(pbar)

        with Parallelized(_build_enum, args=args, num_threads=self._parallel_workers("enums"), desc="Enums", progress=progress_cls) as tasks:
            completed = 0
            for kwargs, built, error in tasks:
                if error:
                    tasks._handle_interrupt()
                    raise error
                col_kl = kwargs["col_kl"]
                for signed_kl in built:
                    signed_kl.link(dir="object", rel="in_column", kl=col_kl)
                enum_kls.extend(built)
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.ENUMS),
                    "step_progress": completed / total_cols,
                    "step_current": completed,
                    "step_total": total_cols,
                    "message": f"kb.extractingEnumsFor:{kwargs['col_name']}",
                }

        return enum_kls

    def build_descriptions(
        self,
        db_kl: DatabaseUKFT,
        tab_kls: Dict[str, TableUKFT],
        col_kls: Dict[str, ColumnUKFT],
        col_kls_by_tab: Dict[str, Dict[str, ColumnUKFT]],
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """\
        Generate descriptions for database, tables, and columns.

        Args:
            db_kl: Database KL.
            tab_kls: Dict of table KLs.
            col_kls: Dict of column KLs.
            col_kls_by_tab: Columns organized by table.
            progress: Optional progress tracker.

        Yields:
            Progress events.
        """
        descs = {"db": None, "tabs": dict(), "cols": dict()}
        cache_path = pj(self.path, "cached_descs.json")
        if exists_file(cache_path):
            descs = load_json(cache_path)

        desc_total = 1 + len(tab_kls) + len(col_kls)
        pbar = progress or NoProgress(total=0, desc="Descriptions")
        pbar.reset(total=desc_total)
        progress_cls = type(pbar)

        cache_dirty = False
        completed = 0

        # Database description
        if descs.get("db") is not None:
            db_kl.description = descs["db"]
        else:
            descs["db"] = db_kl.gen_desc(tab_kls=list(tab_kls.values()))
            cache_dirty = True
        completed += 1
        pbar.update(1)
        yield {
            "stage": str(BuildStage.DESCRIPTIONS),
            "step_progress": completed / desc_total,
            "step_current": completed,
            "step_total": desc_total,
            "message": "kb.database",
        }

        # Table descriptions - cached
        missing_tabs = []
        for tab_id, tab_kl in tab_kls.items():
            cached_desc = descs.get("tabs", dict()).get(tab_id)
            if cached_desc is not None:
                tab_kl.description = cached_desc
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.DESCRIPTIONS),
                    "step_progress": completed / desc_total,
                    "step_current": completed,
                    "step_total": desc_total,
                    "message": f"kb.genTableDesc:{tab_id}",
                }
            else:
                missing_tabs.append((tab_id, tab_kl))

        # Table descriptions - generate missing
        if missing_tabs:

            def _gen_tab_desc(tab_id: str, tab_kl: TableUKFT) -> Tuple[str, str]:
                return tab_id, tab_kl.gen_desc(db_kl=db_kl, col_kls=list(col_kls_by_tab.get(tab_id, dict()).values()))

            args = [{"tab_id": tid, "tab_kl": tkl} for tid, tkl in missing_tabs]
            with Parallelized(_gen_tab_desc, args=args, num_threads=self._parallel_workers("descs_tabs"), desc="Tab Descs", progress=progress_cls) as tasks:
                for kwargs, result, error in tasks:
                    if error:
                        tasks._handle_interrupt()
                        raise error
                    tab_id, desc_val = result
                    tab_kls[tab_id].description = desc_val
                    descs.setdefault("tabs", dict())[tab_id] = desc_val
                    cache_dirty = True
                    completed += 1
                    pbar.update(1)
                    yield {
                        "stage": str(BuildStage.DESCRIPTIONS),
                        "step_progress": completed / desc_total,
                        "step_current": completed,
                        "step_total": desc_total,
                        "message": f"kb.genTableDesc:{tab_id}",
                    }

        # Column descriptions - cached
        missing_cols = []
        for col_name, col_kl in col_kls.items():
            cached_desc = descs.get("cols", dict()).get(col_name)
            if cached_desc is not None:
                col_kl.description = cached_desc
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.DESCRIPTIONS),
                    "step_progress": completed / desc_total,
                    "step_current": completed,
                    "step_total": desc_total,
                    "message": f"kb.genColDesc:{col_name}",
                }
            else:
                missing_cols.append((col_name, col_kl))

        # Column descriptions - generate missing
        if missing_cols:

            def _gen_col_desc(col_name: str, col_kl: ColumnUKFT) -> Tuple[str, str]:
                return col_name, col_kl.gen_desc(db_kl=db_kl, tab_kl=tab_kls.get(col_kl.tab_id))

            args = [{"col_name": cn, "col_kl": ckl} for cn, ckl in missing_cols]
            with Parallelized(_gen_col_desc, args=args, num_threads=self._parallel_workers("descs_cols"), desc="Col Descs", progress=progress_cls) as tasks:
                for kwargs, result, error in tasks:
                    if error:
                        tasks._handle_interrupt()
                        raise error
                    col_name, desc_val = result
                    col_kls[col_name].description = desc_val
                    descs.setdefault("cols", dict())[col_name] = desc_val
                    cache_dirty = True
                    completed += 1
                    pbar.update(1)
                    yield {
                        "stage": str(BuildStage.DESCRIPTIONS),
                        "step_progress": completed / desc_total,
                        "step_current": completed,
                        "step_total": desc_total,
                        "message": f"kb.genColDesc:{col_name}",
                    }

        if cache_dirty:
            save_json(descs, cache_path)

    def build_synonyms(
        self,
        db_kl: DatabaseUKFT,
        tab_kls: Dict[str, TableUKFT],
        col_kls: Dict[str, ColumnUKFT],
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """\
        Generate synonyms for tables and columns.

        Args:
            db_kl: Database KL.
            tab_kls: Dict of table KLs.
            col_kls: Dict of column KLs.
            progress: Optional progress tracker.

        Yields:
            Progress events.
        """
        synonyms = {"tabs": dict(), "cols": dict(), "enums": dict()}
        cache_path = pj(self.path, "cached_synonyms.json")
        if exists_file(cache_path):
            synonyms = load_json(cache_path)

        syn_total = len(tab_kls) + len(col_kls)
        pbar = progress or NoProgress(total=0, desc="Synonyms")
        pbar.reset(total=syn_total)
        progress_cls = type(pbar)

        cache_dirty = False
        completed = 0

        # Table synonyms - cached
        missing_tabs = []
        for tab_id, tab_kl in tab_kls.items():
            cached_syns = synonyms.get("tabs", dict()).get(tab_id)
            if cached_syns is not None:
                tab_kl.synonyms = tab_kl.synonyms.union(set(cached_syns))
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.SYNONYMS),
                    "step_progress": completed / max(syn_total, 1),
                    "step_current": completed,
                    "step_total": syn_total,
                    "message": f"kb.genTableSyns:{tab_id}",
                }
            else:
                missing_tabs.append((tab_id, tab_kl))

        # Table synonyms - generate missing
        if missing_tabs:

            def _gen_tab_syn(tab_id: str, tab_kl: TableUKFT) -> Tuple[str, List[str]]:
                return tab_id, list(tab_kl.gen_syns(db_kl=db_kl))

            args = [{"tab_id": tid, "tab_kl": tkl} for tid, tkl in missing_tabs]
            with Parallelized(_gen_tab_syn, args=args, num_threads=self._parallel_workers("syns_tabs"), desc="Tab Syns", progress=progress_cls) as tasks:
                for kwargs, result, error in tasks:
                    if error:
                        tasks._handle_interrupt()
                        raise error
                    tab_id, syn_list = result
                    tab_kls[tab_id].synonyms = tab_kls[tab_id].synonyms.union(set(syn_list))
                    synonyms.setdefault("tabs", dict())[tab_id] = syn_list
                    cache_dirty = True
                    completed += 1
                    pbar.update(1)
                    yield {
                        "stage": str(BuildStage.SYNONYMS),
                        "step_progress": completed / max(syn_total, 1),
                        "step_current": completed,
                        "step_total": syn_total,
                        "message": f"kb.genTableSyns:{tab_id}",
                    }

        # Column synonyms - cached
        missing_cols = []
        for col_name, col_kl in col_kls.items():
            cached_syns = synonyms.get("cols", dict()).get(col_name)
            if cached_syns is not None:
                col_kl.synonyms = col_kl.synonyms.union(set(cached_syns))
                completed += 1
                pbar.update(1)
                yield {
                    "stage": str(BuildStage.SYNONYMS),
                    "step_progress": completed / max(syn_total, 1),
                    "step_current": completed,
                    "step_total": syn_total,
                    "message": f"kb.genColSyns:{col_name}",
                }
            else:
                missing_cols.append((col_name, col_kl))

        # Column synonyms - generate missing
        if missing_cols:

            def _gen_col_syn(col_name: str, col_kl: ColumnUKFT) -> Tuple[str, List[str]]:
                return col_name, list(col_kl.gen_syns(db_kl=db_kl, tab_kl=tab_kls.get(col_kl.tab_id), col_kl=col_kl))

            args = [{"col_name": cn, "col_kl": ckl} for cn, ckl in missing_cols]
            with Parallelized(_gen_col_syn, args=args, num_threads=self._parallel_workers("syns_cols"), desc="Col Syns", progress=progress_cls) as tasks:
                for kwargs, result, error in tasks:
                    if error:
                        tasks._handle_interrupt()
                        raise error
                    col_name, syn_list = result
                    col_kls[col_name].synonyms = col_kls[col_name].synonyms.union(set(syn_list))
                    synonyms.setdefault("cols", dict())[col_name] = syn_list
                    cache_dirty = True
                    completed += 1
                    pbar.update(1)
                    yield {
                        "stage": str(BuildStage.SYNONYMS),
                        "step_progress": completed / max(syn_total, 1),
                        "step_current": completed,
                        "step_total": syn_total,
                        "message": f"kb.genColSyns:{col_name}",
                    }

        if cache_dirty:
            save_json(synonyms, cache_path)

    def clear_kls(
        self,
        types: List[str] = None,
        batch_size: int = 256,
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, int]:
        """\
        Clear specific types of KLs from main storage.

        Args:
            types: List of KL types to clear. Defaults to database/table/column/enum types.
            batch_size: Batch size for removal.
            progress: Optional progress tracker.

        Yields:
            Progress events.

        Returns:
            Number of KLs cleared.
        """
        if types is None:
            types = [
                DatabaseUKFT.type_default,
                TableUKFT.type_default,
                ColumnUKFT.type_default,
                EnumUKFT.type_default,
            ]

        storage = self.storages.get("main")
        if not storage:
            logger.warning("Main storage not found, skipping clear_kls.")
            return 0

        ids_to_remove = [kl.id for kl in storage if kl.type in types]
        total = len(ids_to_remove)

        if total == 0:
            yield {
                "stage": str(BuildStage.CLEAR),
                "step_progress": 1.0,
                "step_current": 0,
                "step_total": 0,
                "message": "kb.clearedOldKnowledge",
            }
            return 0

        pbar = progress or NoProgress(total=0, desc="Clear")
        pbar.reset(total=total)

        done = 0
        for i in range(0, total, batch_size):
            batch = ids_to_remove[i : min(i + batch_size, total)]
            self.batch_remove(batch)
            done += len(batch)
            pbar.update(len(batch))
            yield {
                "stage": str(BuildStage.CLEAR),
                "step_progress": done / total,
                "step_current": done,
                "step_total": total,
                "message": f"kb.clearingProgress:{done}:{total}",
            }

        return done

    def upsert_kls(
        self,
        db_kl: DatabaseUKFT,
        tab_kls: Dict[str, TableUKFT],
        col_kls: Dict[str, ColumnUKFT],
        enum_kls: List[EnumUKFT],
        batch_size: int = 256,
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, int]:
        """\
        Upsert all KLs to storage.

        Args:
            db_kl: Database KL.
            tab_kls: Dict of table KLs.
            col_kls: Dict of column KLs.
            enum_kls: List of enum KLs.
            batch_size: Batch size for enum upserting.
            progress: Optional progress tracker.

        Yields:
            Progress events.

        Returns:
            Total number of KLs upserted.
        """
        total_units = 1 + len(tab_kls) + len(col_kls) + len(enum_kls)
        pbar = progress or NoProgress(total=0, desc="Upsert")
        pbar.reset(total=total_units)

        done = 0

        # Database
        logger.info("Upserting 1 Database KL")
        self.batch_upsert([db_kl])
        done += 1
        pbar.update(1)
        yield {
            "stage": str(BuildStage.UPSERT),
            "step_progress": done / total_units,
            "step_current": done,
            "step_total": total_units,
            "message": "kb.upsertProgress:db:1/1",
        }

        # Tables
        logger.info(f"Upserting {len(tab_kls)} Table KLs")
        self.batch_upsert(list(tab_kls.values()))
        done += len(tab_kls)
        pbar.update(len(tab_kls))
        yield {
            "stage": str(BuildStage.UPSERT),
            "step_progress": done / total_units,
            "step_current": done,
            "step_total": total_units,
            "message": f"kb.upsertProgress:tabs:{len(tab_kls)}/{len(tab_kls)}",
        }

        # Columns
        logger.info(f"Upserting {len(col_kls)} Column KLs")
        self.batch_upsert(list(col_kls.values()))
        done += len(col_kls)
        pbar.update(len(col_kls))
        yield {
            "stage": str(BuildStage.UPSERT),
            "step_progress": done / total_units,
            "step_current": done,
            "step_total": total_units,
            "message": f"kb.upsertProgress:cols:{len(col_kls)}/{len(col_kls)}",
        }

        # Enums (batched)
        logger.info(f"Upserting {len(enum_kls)} Enum KLs")
        enum_done = 0
        for i in range(0, len(enum_kls), batch_size):
            batch = enum_kls[i : min(i + batch_size, len(enum_kls))]
            self.batch_upsert(batch)
            done += len(batch)
            enum_done += len(batch)
            pbar.update(len(batch))
            yield {
                "stage": str(BuildStage.UPSERT),
                "step_progress": done / total_units,
                "step_current": done,
                "step_total": total_units,
                "message": f"kb.upsertProgress:enums:{enum_done}/{len(enum_kls)}",
            }

        return done

    def build_daac(
        self,
        batch_size: int = 256,
        progress: Progress = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """\
        Build ALL DAAC engines (all desynced engines).

        This builds all engines marked as desync, including 'ac' and 'ac-enums'.
        Called during full build.

        Args:
            batch_size: Batch size for processing.
            progress: Optional progress tracker.

        Yields:
            Progress events.
        """
        # Get all desynced engines
        desynced_engines = [ename for ename in self.desync if ename in self.engines]

        if not desynced_engines:
            yield {
                "stage": str(BuildStage.DAAC),
                "step_progress": 1.0,
                "message": "kb.noEngineToSync",
            }
            return

        # Calculate total items across all engines
        engine_totals = {}
        grand_total = 0
        for ename in desynced_engines:
            engine = self.engines[ename]
            engine.clear()
            engine_total = len(engine.storage) if hasattr(engine, "storage") else 0
            engine_totals[ename] = engine_total
            grand_total += engine_total

        if grand_total == 0:
            yield {
                "stage": str(BuildStage.DAAC),
                "step_progress": 1.0,
                "message": "kb.daacBuilt",
            }
            return

        pbar = progress or NoProgress(total=0, desc="DAAC")
        pbar.reset(total=grand_total)

        done = 0
        for ename in desynced_engines:
            engine = self.engines[ename]
            engine_total = engine_totals[ename]

            if engine_total > 0:
                batch_iter = engine.storage.batch_iter(batch_size=batch_size)
                for kl_batch in batch_iter:
                    engine.batch_upsert(kl_batch, flush=False, progress=None)
                    done += len(kl_batch)
                    pbar.update(len(kl_batch))
                    yield {
                        "stage": str(BuildStage.DAAC),
                        "step_progress": done / grand_total,
                        "step_current": done,
                        "step_total": grand_total,
                        "message": f"kb.syncingDaac:{ename}:{done}:{grand_total}",
                    }

                engine.flush()

        yield {
            "stage": str(BuildStage.DAAC),
            "step_progress": 1.0,
            "step_current": grand_total,
            "step_total": grand_total,
            "message": "kb.daacBuilt",
        }

    # =========================================================================
    # Public API: Sync Stream (only syncs 'ac' engine, not 'ac-enums')
    # =========================================================================

    def sync_stream(
        self,
        batch_size: int = None,
        progress: Type[Progress] = None,
    ) -> Iterable[Dict[str, Any]]:
        """\
        Sync the 'ac' engine only (not 'ac-enums').

        This is used for incremental sync after KB is built. The 'ac-enums'
        engine is only populated during full build.

        Args:
            batch_size: Batch size for processing.
            progress: Progress class for display.

        Yields:
            Progress events.
        """
        start_time = time.time()
        status_progress_cls = progress or RubikSQLRichProgress
        batch_size = batch_size or RUBIK_CM.get("klbase.batch_size", 256)

        # Only sync the 'ac' engine
        engine_name = "ac"

        with status_progress_cls(desc="RubikSQL Sync") as status_pbar:

            def emit(payload: Dict[str, Any]) -> Dict[str, Any]:
                event = dict(payload)
                event.setdefault("status", "syncing")
                event["elapsed"] = round(time.time() - start_time, 1)
                emitted = status_pbar.emit(event)
                return emitted if emitted is not None else event

            # Check if engine exists and is desynced
            if engine_name not in self.engines:
                yield emit(
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "kb.noEngineToSync",
                        "step": "kb.step.completed",
                        "step_progress": 1.0,
                    }
                )
                return

            if engine_name not in self.desync:
                yield emit(
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "kb.engineAlreadySynced",
                        "step": "kb.step.completed",
                        "step_progress": 1.0,
                    }
                )
                return

            engine = self.engines[engine_name]
            engine.clear()
            engine_total = len(engine.storage) if hasattr(engine, "storage") else 0

            if engine_total == 0:
                yield emit(
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "kb.syncCompleted",
                        "step": "kb.step.completed",
                        "step_progress": 1.0,
                    }
                )
                return

            yield emit(
                {
                    "progress": 0.0,
                    "message": f"kb.syncingEngine:{engine_name}",
                    "step": "kb.step.daacEngine",
                    "step_progress": 0.0,
                    "step_current": 0,
                    "step_total": engine_total,
                }
            )

            done = 0
            batch_iter = engine.storage.batch_iter(batch_size=batch_size)
            for kl_batch in batch_iter:
                engine.batch_upsert(kl_batch, flush=False, progress=None)
                done += len(kl_batch)
                yield emit(
                    {
                        "progress": done / engine_total,
                        "message": f"kb.syncingDaac:{done}:{engine_total}",
                        "step": "kb.step.daacEngine",
                        "step_progress": done / engine_total,
                        "step_current": done,
                        "step_total": engine_total,
                    }
                )

            engine.flush()

            yield emit(
                {
                    "status": "completed",
                    "progress": 1.0,
                    "message": "kb.syncCompleted",
                    "step": "kb.step.completed",
                    "step_progress": 1.0,
                }
            )

    # Keep old name for compatibility
    def sync_desynced_stream(self, progress: Type[Progress] = None, **kwargs) -> Iterable[Dict[str, Any]]:
        """Alias for sync_stream for backward compatibility."""
        return self.sync_stream(progress=progress, **kwargs)

    # =========================================================================
    # Public API: Composite Build
    # =========================================================================

    def build_stream(
        self,
        force: bool = False,
        stages: Optional[List[str]] = None,
        progress: Type[Progress] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Iterable[Dict[str, Any]]:
        """\
        Streamlined build process with modular stages.

        Args:
            force: Force rebuild even if already built.
            stages: Optional list of stage names to run. If None, runs all stages.
            progress: Progress class for display.
            cancel: Optional cancellation callback.

        Yields:
            Progress events with status, progress, message, step fields.
        """
        start_time = time.time()
        status_progress_cls = progress or RubikSQLRichProgress
        batch_size = RUBIK_CM.get("klbase.batch_size", 256)

        # Build state management
        build_state_path = pj(self.path, "build.json")
        build_state = load_json(build_state_path) if exists_file(build_state_path) else {}

        # Determine which stages to run
        if stages is None:
            stages_to_run = STAGE_ORDER
        else:
            stages_to_run = [BuildStage(s) for s in stages if s in [str(st) for st in STAGE_ORDER]]

        # Get stage weights for progress calculation
        normalized_weights = get_normalized_weights(stages_to_run)
        accumulated_weights = get_accumulated_weights(stages_to_run)

        def _stage_progress(stage: BuildStage, idx: int, total: int) -> float:
            key = str(stage)
            if total <= 0:
                return accumulated_weights.get(key, 0.0)
            return accumulated_weights.get(key, 0.0) + normalized_weights.get(key, 0.0) * (idx / total)

        def _save_build(status: str, progress_val: float, step: str, stage_statuses: Dict = None):
            state = {
                "status": status,
                "progress": progress_val,
                "step": step,
                "timestamp": time.time(),
            }
            if stage_statuses:
                state["stages"] = stage_statuses
            save_json(state, build_state_path)

        def _cancelled() -> bool:
            return bool(cancel and cancel())

        # Track stage completion
        stage_statuses = build_state.get("stages", {})

        with status_progress_cls(desc="RubikSQL Build") as status_pbar:

            def emit(payload: Dict[str, Any]) -> Dict[str, Any]:
                event = dict(payload)
                event.setdefault("status", "building")
                event["elapsed"] = round(time.time() - start_time, 1)
                emitted = status_pbar.emit(event)
                return emitted if emitted is not None else event

            # Check if already built (only if running all stages)
            if (not force) and stages is None and build_state.get("status") == "completed":
                yield emit(
                    {
                        "status": "completed",
                        "progress": 1.0,
                        "message": "kb.alreadyBuilt",
                        "step": "kb.step.completed",
                        "step_progress": 1.0,
                    }
                )
                return

            # Get DB info
            db_id = next(iter(self.info.get("db_info", dict())), None)
            tabs_info = self.info.get("tabs_info", dict())
            cols_info = self.info.get("cols_info", dict())

            # Shared context for stages
            db_kl = None
            tab_kls = {}
            col_kls = {}
            col_kls_by_tab = {}
            enum_kls = []
            row_counts = {}

            # Run each stage
            for stage in stages_to_run:
                stage_key = str(stage)

                # Check if stage should be skipped (already done and not forcing)
                if not force and stage_statuses.get(stage_key, {}).get("status") == "completed":
                    # Still need to load results for dependent stages
                    # Skip for now - full implementation would reload from cache
                    pass

                if _cancelled():
                    yield emit(
                        {
                            "status": "cancelled",
                            "progress": accumulated_weights.get(stage_key, 0.0),
                            "step": f"kb.step.{stage_key}",
                        }
                    )
                    return

                # Run stage
                if stage == BuildStage.COUNT:
                    for tab_id in tabs_info.keys():
                        row_counts[tab_id] = self.db.row_count(tab_id)
                    yield emit(
                        {
                            "progress": _stage_progress(stage, 1, 1),
                            "message": "kb.counting",
                            "step": "kb.step.counting",
                            "step_progress": 1.0,
                        }
                    )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}

                elif stage == BuildStage.DATABASE:
                    gen = self.build_database_kl(db_id)
                    while True:
                        try:
                            event = next(gen)
                            yield emit(
                                {
                                    "progress": _stage_progress(stage, 1, 1),
                                    "message": event.get("message"),
                                    "step": "kb.step.databaseKl",
                                    "step_progress": event.get("step_progress", 0.0),
                                }
                            )
                        except StopIteration as e:
                            db_kl = e.value
                            break
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}

                elif stage == BuildStage.TABLES:
                    if db_kl is None:
                        raise ValueError("Database KL not built yet")
                    tab_count = len(tabs_info)
                    gen = self.build_table_kls(db_kl)
                    while True:
                        try:
                            event = next(gen)
                            yield emit(
                                {
                                    "progress": _stage_progress(stage, event.get("step_current", 0), max(tab_count, 1)),
                                    "message": event.get("message"),
                                    "step": "kb.step.tableKls",
                                    "step_progress": event.get("step_progress", 0.0),
                                    "step_current": event.get("step_current"),
                                    "step_total": event.get("step_total"),
                                }
                            )
                        except StopIteration as e:
                            tab_kls = e.value
                            break
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, tab_count, max(tab_count, 1)), "kb.step.tableKls", stage_statuses)

                elif stage == BuildStage.COLUMNS:
                    if db_kl is None or not tab_kls:
                        raise ValueError("Database and table KLs not built yet")
                    col_count = len(cols_info)
                    gen = self.build_column_kls(db_kl, tab_kls)
                    while True:
                        try:
                            event = next(gen)
                            yield emit(
                                {
                                    "progress": _stage_progress(stage, event.get("step_current", 0), max(col_count, 1)),
                                    "message": event.get("message"),
                                    "step": "kb.step.columnKls",
                                    "step_progress": event.get("step_progress", 0.0),
                                    "step_current": event.get("step_current"),
                                    "step_total": event.get("step_total"),
                                }
                            )
                        except StopIteration as e:
                            col_kls, col_kls_by_tab = e.value
                            break
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, col_count, max(col_count, 1)), "kb.step.columnKls", stage_statuses)

                elif stage == BuildStage.ENUMS:
                    if db_kl is None or not col_kls:
                        raise ValueError("Database and column KLs not built yet")
                    col_count = len(col_kls)
                    gen = self.build_enum_kls(db_kl, col_kls)
                    while True:
                        try:
                            event = next(gen)
                            yield emit(
                                {
                                    "progress": _stage_progress(stage, event.get("step_current", 0), max(col_count, 1)),
                                    "message": event.get("message"),
                                    "step": "kb.step.enumKls",
                                    "step_progress": event.get("step_progress", 0.0),
                                    "step_current": event.get("step_current"),
                                    "step_total": event.get("step_total"),
                                }
                            )
                        except StopIteration as e:
                            enum_kls = e.value
                            break
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, col_count, max(col_count, 1)), "kb.step.enumKls", stage_statuses)

                elif stage == BuildStage.DESCRIPTIONS:
                    if db_kl is None or not tab_kls or not col_kls:
                        raise ValueError("Database, table, and column KLs not built yet")
                    desc_total = 1 + len(tab_kls) + len(col_kls)
                    for event in self.build_descriptions(db_kl, tab_kls, col_kls, col_kls_by_tab):
                        yield emit(
                            {
                                "progress": _stage_progress(stage, event.get("step_current", 0), max(desc_total, 1)),
                                "message": event.get("message"),
                                "step": "kb.step.descriptions",
                                "step_progress": event.get("step_progress", 0.0),
                                "step_current": event.get("step_current"),
                                "step_total": event.get("step_total"),
                            }
                        )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, desc_total, max(desc_total, 1)), "kb.step.descriptions", stage_statuses)

                elif stage == BuildStage.SYNONYMS:
                    if db_kl is None or not tab_kls or not col_kls:
                        raise ValueError("Database, table, and column KLs not built yet")
                    syn_total = len(tab_kls) + len(col_kls)
                    for event in self.build_synonyms(db_kl, tab_kls, col_kls):
                        yield emit(
                            {
                                "progress": _stage_progress(stage, event.get("step_current", 0), max(syn_total, 1)),
                                "message": event.get("message"),
                                "step": "kb.step.synonyms",
                                "step_progress": event.get("step_progress", 0.0),
                                "step_current": event.get("step_current"),
                                "step_total": event.get("step_total"),
                            }
                        )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, syn_total, max(syn_total, 1)), "kb.step.synonyms", stage_statuses)

                elif stage == BuildStage.CLEAR:
                    gen = self.clear_kls(batch_size=batch_size)
                    for event in gen:
                        total = event.get("step_total", 1)
                        yield emit(
                            {
                                "progress": _stage_progress(stage, event.get("step_current", 0), max(total, 1)),
                                "message": event.get("message"),
                                "step": "kb.step.clearKls",
                                "step_progress": event.get("step_progress", 0.0),
                                "step_current": event.get("step_current"),
                                "step_total": event.get("step_total"),
                            }
                        )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}

                elif stage == BuildStage.UPSERT:
                    if db_kl is None:
                        raise ValueError("Database KL not built yet")
                    upsert_total = 1 + len(tab_kls) + len(col_kls) + len(enum_kls)
                    gen = self.upsert_kls(db_kl, tab_kls, col_kls, enum_kls, batch_size=batch_size)
                    for event in gen:
                        yield emit(
                            {
                                "progress": _stage_progress(stage, event.get("step_current", 0), max(upsert_total, 1)),
                                "message": event.get("message"),
                                "step": "kb.step.upsertKls",
                                "step_progress": event.get("step_progress", 0.0),
                                "step_current": event.get("step_current"),
                                "step_total": event.get("step_total"),
                            }
                        )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}
                    _save_build("running", _stage_progress(stage, upsert_total, max(upsert_total, 1)), "kb.step.upsertKls", stage_statuses)

                elif stage == BuildStage.DAAC:
                    for event in self.build_daac(batch_size=batch_size):
                        total = event.get("step_total", 1)
                        yield emit(
                            {
                                "progress": _stage_progress(stage, event.get("step_current", 0), max(total, 1)),
                                "message": event.get("message"),
                                "step": "kb.step.daacEngine",
                                "step_progress": event.get("step_progress", 0.0),
                                "step_current": event.get("step_current"),
                                "step_total": event.get("step_total"),
                            }
                        )
                    stage_statuses[stage_key] = {"status": "completed", "progress": 1.0}

            # Completed
            _save_build("completed", 1.0, "kb.step.completed", stage_statuses)
            yield emit(
                {
                    "status": "completed",
                    "progress": 1.0,
                    "message": "kb.success",
                    "step": "kb.step.completed",
                    "step_progress": 1.0,
                }
            )

    def build(self, force: bool = False, progress: Type[Progress] = None):
        """Build KB using modular steps with optional progress reporting."""
        for _ in self.build_stream(force=force, progress=progress):
            pass

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _parallel_workers(self, stage: str) -> Optional[int]:
        """Resolve worker count for a parallel stage from config."""
        parallel_cfg = RUBIK_CM.get("klbase.parallel", dict())
        build_cfg = RUBIK_CM.get("klbase.build", dict())
        workers = None
        if isinstance(parallel_cfg, dict):
            workers = parallel_cfg.get(stage, None)
            if workers is None:
                workers = parallel_cfg.get("workers", parallel_cfg.get("default", None))
        if workers is None and isinstance(build_cfg, dict):
            workers = build_cfg.get("num_threads", None)
        try:
            return max(1, int(workers)) if workers is not None else None
        except Exception:
            logger.warning(f"Invalid worker count for stage '{stage}': {workers}")
            return None

    def clear_types(self, types: List[str], batch_size: int = 256) -> Generator[Tuple[int, int], None, None]:
        """Clear specific types of KLs from the main storage.

        Args:
            types: List of KL types to clear.
            batch_size: Batch size for removal.

        Yields:
            Tuple[int, int]: (done, total) progress tuple.
        """
        if not types:
            return

        storage = self.storages.get("main")
        if not storage:
            logger.warning("Main storage not found, skipping clear_types.")
            return

        ids_to_remove = [kl.id for kl in storage if kl.type in types]
        total = len(ids_to_remove)

        if not ids_to_remove:
            return

        logger.info(f"Clearing {total} KLs of types {types}")

        done = 0
        for i in range(0, total, batch_size):
            batch = ids_to_remove[i : min(i + batch_size, total)]
            self.batch_remove(batch)
            done += len(batch)
            yield done, total
