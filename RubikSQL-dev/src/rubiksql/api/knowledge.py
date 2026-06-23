"""\
Knowledge base management API for RubikSQL.

This module provides functions for managing knowledge bases.
"""

from typing import Optional, List, Tuple, Any

from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.parallel_utils import Parallelized
from ahvn.ukf.ukf_utils import ptags, gtags

from rubiksql.klbase import RubikSQLKLBase, RUBIK_KBM
from rubiksql.db import RUBIK_DBM, DatabaseInfo
from rubiksql.ukfs.col_ukft import ColumnUKFT
from rubiksql.ukfs.tab_ukft import TableUKFT
from rubiksql.utils.progress_utils import RubikSQLRichProgress
from rubiksql.utils.config_utils import RUBIK_CM

logger = get_logger(__name__)


def load_kb(db_id: str) -> RubikSQLKLBase:
    """\
    Load or get cached KLBase instance for a database.

    Equivalent to: RUBIK_KBM.load(db_id)

    Args:
        db_id: Database identifier (must be registered in RUBIK_DBM).

    Returns:
        RubikSQLKLBase instance (cached or newly created).

    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")
    return RUBIK_KBM.load(db_id)


def purge_kb(db_id: Optional[str] = None) -> int:
    """\
    Purge KLBase instance(s) from cache.

    Equivalent to: RUBIK_KBM.purge(db_id)

    Args:
        db_id: Database identifier. If None, purges all cached instances.

    Returns:
        Number of instances purged.
    """
    return RUBIK_KBM.purge(db_id)


def build_column(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build ColumnUKFT knowledge for columns in the database.

    Supports hierarchical building:
    - If neither tab_id nor col_id specified: build all columns in all tables
    - If tab_id specified but not col_id: build all columns in that table
    - If both tab_id and col_id specified: build that single column

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        col_id: Optional column identifier (requires tab_id).
        update: If True, rebuild even if knowledge already exists.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of columns built.

    Raises:
        ValueError: If database not found, col_id without tab_id, or invalid identifiers.
    """
    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db = RUBIK_DBM.connect(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which columns to build
    columns_to_build: List[Tuple[str, str]] = []

    if tab_id is None:
        # Build all columns in all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            for column in table.columns.values():
                if column.disabled:
                    continue
                columns_to_build.append((table.tab_id, column.col_id))
    elif col_id is None:
        # Build all columns in specified table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        for column in table.columns.values():
            if column.disabled:
                continue
            columns_to_build.append((tab_id, column.col_id))
    else:
        # Build single column
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        column = table.columns.get(col_id)
        if column is None:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")
        if column.disabled:
            raise ValueError(f"Column '{col_id}' is disabled")
        columns_to_build.append((tab_id, col_id))

    if not columns_to_build:
        logger.info(f"No columns to build for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Pre-filter: determine which columns actually need to be built
    columns_to_actually_build: List[Tuple[str, str]] = []
    columns_to_remove: List[str] = []  # entity IDs to remove

    for t_id, c_id in columns_to_build:
        existing = kb.get_entity(tab_id=t_id, col_id=c_id, anchor=False)

        if existing is not None:
            if update:
                # Mark for removal and rebuild
                columns_to_remove.append(existing.id)
                columns_to_actually_build.append((t_id, c_id))
                logger.debug(f"Will remove and rebuild knowledge for {t_id}.{c_id} (id={existing.id})")
            else:
                # Skip existing
                logger.debug(f"Skipping existing column knowledge for {t_id}.{c_id}")
        else:
            # Doesn't exist, needs to be built
            columns_to_actually_build.append((t_id, c_id))

    # Remove existing knowledge if updating
    for entity_id in columns_to_remove:
        kb.remove(entity_id)
        logger.debug(f"Removed existing knowledge (id={entity_id})")

    if not columns_to_actually_build:
        logger.info(f"All columns already built for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Define worker function for parallel processing
    def build_column_worker(t_id: str, c_id: str) -> Optional[ColumnUKFT]:
        """Build a single column's ColumnUKFT."""
        try:
            # Get column metadata
            table = db_info.tables.get(t_id)
            column = table.columns.get(c_id)
            datatype = column.datatype_anno
            enum_index = column.enum_index_enabled
            is_pk = column.is_pk
            fks = [fk for fk in table.fks if fk.get("col_name") == c_id]

            # Build ColumnUKFT with thread-local connection
            col_ukft = ColumnUKFT.from_col(
                db_id=db_id,
                tab_id=t_id,
                col_id=c_id,
                short_description=column.desc or "",
                description=column.desc or "",
                datatype=datatype,
                enum_index=enum_index,
                synonyms=set(),
                is_pk=is_pk,
                fks=fks,
            ).signed(system=True, verified=(not column.disabled))

            logger.debug(f"Built ColumnUKFT for {t_id}.{c_id}")
            return col_ukft
        except Exception as e:
            logger.error(f"Failed to build ColumnUKFT for {t_id}.{c_id}: {e}")
            return None

    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Build columns in parallel
    built_kls: List[ColumnUKFT] = []
    total = len(columns_to_actually_build)

    with Parallelized(
        func=build_column_worker,
        args=[{"t_id": t_id, "c_id": c_id} for t_id, c_id in columns_to_actually_build],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                logger.error(f"Error building column {t_id}.{c_id}: {error}")
                # Continue processing other columns
                continue

            # Collect successful result
            if result is not None:
                built_kls.append(result)

            # Update progress outside parallelism
            if progress is not None:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built column {t_id}.{c_id}",
                        "step": "build_column",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )

    # Batch upsert all built knowledge
    if built_kls:
        kb.batch_upsert(built_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(built_kls)} columns")

    built_count = len(built_kls)

    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building {built_count} columns",
                "step": "build_column",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )

    db.close_conn()
    logger.info(f"Built {built_count} columns for db_id='{db_id}'")
    return built_count


def build_table(
    db_id: str,
    tab_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build TableUKFT knowledge for tables in the database.

    Supports hierarchical building:
    - If tab_id not specified: build all tables in the database
    - If tab_id specified: build that single table

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for building a single table.
        update: If True, rebuild even if knowledge already exists.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of tables built.

    Raises:
        ValueError: If database not found or invalid identifiers.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db = RUBIK_DBM.connect(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which tables to build
    tables_to_build: List[str] = []

    if tab_id is None:
        # Build all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            tables_to_build.append(table.tab_id)
    else:
        # Build single table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        tables_to_build.append(tab_id)

    if not tables_to_build:
        logger.info(f"No tables to build for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Pre-filter: determine which tables actually need to be built
    tables_to_actually_build: List[str] = []
    tables_to_remove: List[str] = []  # entity IDs to remove

    for t_id in tables_to_build:
        existing = kb.get_entity(tab_id=t_id, anchor=False)

        if existing is not None:
            if update:
                # Mark for removal and rebuild
                tables_to_remove.append(existing.id)
                tables_to_actually_build.append(t_id)
                logger.debug(f"Will remove and rebuild knowledge for {t_id} (id={existing.id})")
            else:
                # Skip existing
                logger.debug(f"Skipping existing table knowledge for {t_id}")
        else:
            # Doesn't exist, needs to be built
            tables_to_actually_build.append(t_id)

    # Remove existing knowledge if updating
    for entity_id in tables_to_remove:
        kb.remove(entity_id)
        logger.debug(f"Removed existing knowledge (id={entity_id})")

    if not tables_to_actually_build:
        logger.info(f"All tables already built for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Define worker function for parallel processing
    def build_table_worker(t_id: str) -> Optional[TableUKFT]:
        """Build a single table's TableUKFT."""
        try:
            # Get table metadata
            table = db_info.tables.get(t_id)

            tab_ukft = TableUKFT.from_tab(
                db_id=db_id,
                tab_id=t_id,
                short_description=table.desc or "",
                description=table.desc or "",
                synonyms=set(),
            ).signed(system=True, verified=(not table.disabled))

            logger.debug(f"Built TableUKFT for {t_id}")
            return tab_ukft
        except Exception as e:
            logger.error(f"Failed to build TableUKFT for {t_id}: {e}")
            return None

    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Build tables in parallel
    built_kls: List["TableUKFT"] = []
    total = len(tables_to_actually_build)

    with Parallelized(
        func=build_table_worker,
        args=[{"t_id": t_id} for t_id in tables_to_actually_build],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                logger.error(f"Error building table {t_id}: {error}")
                # Continue processing other tables
                continue

            # Collect successful result
            if result is not None:
                built_kls.append(result)

            # Update progress outside parallelism
            if progress is not None:
                t_id = kwargs.get("t_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built table {t_id}",
                        "step": "build_table",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )

    # Batch upsert all built knowledge
    if built_kls:
        kb.batch_upsert(built_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(built_kls)} tables")

    built_count = len(built_kls)

    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building {built_count} tables",
                "step": "build_table",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )

    db.close_conn()
    logger.info(f"Built {built_count} tables for db_id='{db_id}'")
    return built_count


def build_database(
    db_id: str,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build DatabaseUKFT knowledge for the database.

    Args:
        db_id: Database identifier.
        update: If True, rebuild even if knowledge already exists.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of databases built (0 or 1).

    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db = RUBIK_DBM.connect(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Check if database knowledge already exists
    existing = kb.get_entity(anchor=False)

    if existing is not None and not update:
        logger.info(f"Database knowledge already exists for db_id='{db_id}'")
        db.close_conn()
        return 0

    # Remove existing knowledge if updating
    if existing is not None:
        kb.remove(existing.id)
        logger.debug(f"Removed existing database knowledge (id={existing.id})")

    # Update progress
    if progress is not None:
        progress.emit(
            {
                "progress": 0.5,
                "message": f"Building database {db_id}",
                "step": "build_database",
                "step_current": 1,
                "step_total": 1,
                "status": "building",
            }
        )

    # Build DatabaseUKFT
    try:
        from rubiksql.ukfs.db_ukft import DatabaseUKFT

        db_ukft = DatabaseUKFT.from_db(
            db_id=db_id,
            short_description=db_info.desc or "",
            description=db_info.desc or "",
            synonyms=set(),
        ).signed(system=True, verified=(not db_info.disabled))

        logger.debug(f"Built DatabaseUKFT for {db_id}")

        # Upsert the knowledge
        kb.upsert(db_ukft)
        logger.info(f"Upserted database knowledge for db_id='{db_id}'")

        built_count = 1
    except Exception as e:
        logger.error(f"Failed to build DatabaseUKFT for {db_id}: {e}")
        built_count = 0

    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building database {db_id}",
                "step": "build_database",
                "step_current": 1,
                "step_total": 1,
                "status": "completed",
            }
        )

    db.close_conn()
    logger.info(f"Built database knowledge for db_id='{db_id}'")
    return built_count


def build_column_type(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Deduce and update column datatypes in the knowledge base.

    Uses deduction rules and LLM calls (for datetime parsing) to infer column types.
    Updates both the ColumnUKFT knowledge and the db_info.yaml metadata.

    Deduction logic:
    1. If existing type is not null and not updating, skip.
    2. If updating, always prefer user datatype (datatype_anno) if provided.
    3. Apply deduction rules: datetime (with LLM), identifier, categorical, integer, float, text, etc.

    Supports hierarchical building:
    - If neither tab_id nor col_id specified: process all columns in all tables
    - If tab_id specified but not col_id: process all columns in that table
    - If both tab_id and col_id specified: process that single column

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        col_id: Optional column identifier (requires tab_id).
        update: If True, re-deduce types even if already deduced.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of columns with types deduced.

    Raises:
        ValueError: If database not found, col_id without tab_id, or invalid identifiers.
    """
    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which columns to process
    columns_to_process: List[Tuple[str, str]] = []

    if tab_id is None:
        # Process all columns in all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            for column in table.columns.values():
                if column.disabled:
                    continue
                columns_to_process.append((table.tab_id, column.col_id))
    elif col_id is None:
        # Process all columns in specified table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        for column in table.columns.values():
            if column.disabled:
                continue
            columns_to_process.append((tab_id, column.col_id))
    else:
        # Process single column
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        column = table.columns.get(col_id)
        if column is None:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")
        if column.disabled:
            raise ValueError(f"Column '{col_id}' is disabled")
        columns_to_process.append((tab_id, col_id))

    if not columns_to_process:
        logger.info(f"No columns to process for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Pre-filter: determine which columns need type deduction
    columns_to_actually_process: List[Tuple[str, str, ColumnUKFT]] = []

    for t_id, c_id in columns_to_process:
        existing = kb.get_entity(tab_id=t_id, col_id=c_id, anchor=False)

        if existing is None:
            logger.warning(f"Column knowledge not found for {t_id}.{c_id}. Run 'rubiksql build column' first.")
            continue

        # Check if type already deduced
        current_datatype = existing.get("datatype", "UNKNOWN")
        table = db_info.tables.get(t_id)
        column = table.columns.get(c_id)

        # Skip if type is already deduced and not updating
        if current_datatype != "UNKNOWN" and not update:
            logger.debug(f"Skipping {t_id}.{c_id} - type already deduced: {current_datatype}")
            continue

        # Check if user provided datatype_anno
        if update and column.datatype_anno:
            # User annotation takes priority - we'll use it directly
            logger.debug(f"Will use user-provided datatype for {t_id}.{c_id}: {column.datatype_anno}")

        columns_to_actually_process.append((t_id, c_id, existing))

    if not columns_to_actually_process:
        logger.info(f"All columns already have types deduced for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Define worker function for processing
    def deduce_type_worker(t_id: str, c_id: str, col_ukft: ColumnUKFT) -> Optional[ColumnUKFT]:
        """Deduce type for a single column."""
        try:
            # Get table/column metadata
            table = db_info.tables.get(t_id)
            column = table.columns.get(c_id)

            # Determine the datatype to use
            if column.datatype_anno:
                # Priority 1: User-provided datatype_anno
                logger.debug(f"Using user-provided datatype for {t_id}.{c_id}: {column.datatype_anno}")
                datatype = column.datatype_anno
                extra_stats = {}
            else:
                # Priority 2: Auto-deduce
                logger.debug(f"Auto-deducing datatype for {t_id}.{c_id}")
                datatype_enum, extra_stats = col_ukft.type_deduction(overwrite=update)
                datatype = datatype_enum.value

            updated_ukft = col_ukft.type_annotated(datatype=datatype, **extra_stats)

            logger.debug(f"Deduced type for {t_id}.{c_id}: {datatype}")
            return (col_ukft.id, updated_ukft)
        except Exception as e:
            logger.error(f"Failed to deduce type for {t_id}.{c_id}: {e}")
            return None

    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Process columns in parallel
    original_kids: List[int] = []
    updated_kls: List[ColumnUKFT] = []
    total = len(columns_to_actually_process)

    with Parallelized(
        func=deduce_type_worker,
        args=[{"t_id": t_id, "c_id": c_id, "col_ukft": col_ukft} for t_id, c_id, col_ukft in columns_to_actually_process],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                logger.error(f"Error deducing type for column {t_id}.{c_id}: {error}")
                continue

            # Collect successful result
            if result is not None:
                original_kids.append(result[0])
                updated_kls.append(result[1])

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Deduced type for {t_id}.{c_id}",
                        "step": "build_column_type",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )

    # Batch upsert all updated knowledge
    if updated_kls:
        kb.batch_remove(original_kids, batch_size=batch_size)
        kb.batch_upsert(updated_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(updated_kls)} column types")

        # Update db_info with deduced datatypes
        for ukft in updated_kls:
            t_id = ukft.get("tab_id")
            c_id = ukft.get("col_id")
            datatype = ukft.get("datatype")
            
            if t_id in db_info.tables and c_id in db_info.tables[t_id].columns:
                db_info.tables[t_id].columns[c_id].datatype_anno = datatype
                logger.debug(f"Updated db_info datatype_anno for {t_id}.{c_id}: {datatype}")
        
        # Save updated db_info
        RUBIK_DBM.save_db_info(db_id, db_info)
        logger.info(f"Saved updated db_info for {len(updated_kls)} columns")

    updated_count = len(updated_kls)

    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed type deduction for {updated_count} columns",
                "step": "build_column_type",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )

    logger.info(f"Deduced types for {updated_count} columns in db_id='{db_id}'")
    return updated_count


def build_database_desc(
    db_id: str,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build or update database description in the knowledge base.

    Uses LLM calls to generate or refine database description based on table and column summaries.

    Args:
        db_id: Database identifier.
        update: If True, re-generate description even if already present.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        1 if description built or updated, 0 otherwise.
    
    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Get existing database knowledge
    existing = kb.get_entity(anchor=False)

    if existing is not None:
        current_desc = existing.description
        if current_desc and not update:
            logger.info(f"Database description already exists for db_id='{db_id}'")
            return 0

    # Update progress
    if progress is not None:
        progress.emit(
            {
                "progress": 0.5,
                "message": f"Building database description for {db_id}",
                "step": "build_database_desc",
                "step_current": 1,
                "step_total": 1,
                "status": "building",
            }
        )
    # Build or update description
    try:
        db_kl = existing
        # Get all table knowledge
        tab_kls = [kb.get_entity(tab_id=table.tab_id, anchor=False) for table in db_info.tables.values()]
        db_desc = db_kl.gen_desc(tab_kls=tab_kls)
        db_kl.description = db_desc
        # Upsert the updated knowledge
        kb.remove(db_kl.id)
        kb.upsert(db_kl)
        logger.info(f"Upserted database description for db_id='{db_id}'")
        # Update db_info with database description
        db_info.desc = db_desc
        RUBIK_DBM.save_db_info(db_id, db_info)
        logger.info(f"Saved updated db_info description for db_id='{db_id}'")
        built_count = 1
    except Exception as e:
        logger.error(f"Failed to build database description for {db_id}: {e}")
        built_count = 0
        
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building database description for {db_id}",
                "step": "build_database_desc",
                "step_current": 1,
                "step_total": 1,
                "status": "completed",
            }
        )

    logger.info(f"Built database description for db_id='{db_id}'")
    return built_count


def build_table_desc(
    db_id: str,
    tab_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build or update table descriptions in the knowledge base.

    Uses LLM calls to generate or refine table descriptions based on column summaries.

    Supports hierarchical building:
    - If tab_id not specified: process all tables
    - If tab_id specified: process that single table

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        update: If True, re-generate descriptions even if already present.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of tables with descriptions built or updated.
    
    Raises:
        ValueError: If database not found or invalid table identifier.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which tables to process
    tables_to_process: List[str] = []

    if tab_id is None:
        # Process all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            tables_to_process.append(table.tab_id)
    else:
        # Process single table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        tables_to_process.append(tab_id)

    if not tables_to_process:
        logger.info(f"No tables to process for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Pre-filter: determine which tables need description building
    tables_to_actually_process: List[Tuple[str, TableUKFT]] = []

    for t_id in tables_to_process:
        existing = kb.get_entity(tab_id=t_id, anchor=False)
    
        if existing is None:
            logger.warning(f"Table knowledge not found for {t_id}. Run 'rubiksql build table' first.")
            continue

        current_desc = existing.description
        if current_desc and not update:
            logger.debug(f"Skipping {t_id} - description already exists")
            continue

        tables_to_actually_process.append((t_id, existing))

    if not tables_to_actually_process:
        logger.info(f"All tables already have descriptions for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Define worker function for processing
    db_kl = kb.get_entity(anchor=False)
    def build_desc_worker(t_id: str, tab_kl: TableUKFT) -> Optional[TableUKFT]:
        """Build or update description for a single table."""
        try:
            # Get all column knowledge for this table
            col_kls = [kb.get_entity(tab_id=t_id, col_id=col.col_id, anchor=False) for col in db_info.tables[t_id].columns.values()]
            tab_desc = tab_kl.gen_desc(db_kl=db_kl, col_kls=col_kls)
            tab_kl.description = tab_desc
            logger.debug(f"Built/Updated description for {t_id}")
            return (tab_kl.id, tab_kl)
        except Exception as e:
            logger.error(f"Failed to build description for {t_id}: {e}")
            return None
    
    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Process tables in parallel
    original_kids: List[int] = []
    updated_kls: List[TableUKFT] = []
    total = len(tables_to_actually_process)
    with Parallelized(
        func=build_desc_worker,
        args=[{"t_id": t_id, "tab_kl": tab_kl} for t_id, tab_kl in tables_to_actually_process],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                logger.error(f"Error building description for table {t_id}: {error}")
                continue

            # Collect successful result
            if result is not None:
                original_kids.append(result[0])
                updated_kls.append(result[1])

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built description for {t_id}",
                        "step": "build_table_desc",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )
    
    # Batch upsert all updated knowledge
    if updated_kls:
        kb.batch_remove(original_kids, batch_size=batch_size)
        kb.batch_upsert(updated_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(updated_kls)} table descriptions")

        for ukft in updated_kls:
            t_id = ukft.get("tab_id")
            desc = ukft.description
            if t_id in db_info.tables:
                db_info.tables[t_id].desc = desc
                logger.debug(f"Updated db_info description for {t_id}")
        RUBIK_DBM.save_db_info(db_id, db_info)
        logger.info(f"Saved updated db_info descriptions for {len(updated_kls)} tables")
    
    built_count = len(updated_kls)
    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building {built_count} table descriptions",
                "step": "build_table_desc",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )
    
    logger.info(f"Built {built_count} table descriptions for db_id='{db_id}'")
    return built_count


def build_column_desc(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build or update column descriptions in the knowledge base.

    Uses LLM calls to generate or refine column descriptions based on data samples.

    Supports hierarchical building:
    - If neither tab_id nor col_id specified: process all columns in all tables
    - If tab_id specified but not col_id: process all columns in that table
    - If both tab_id and col_id specified: process that single column

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        col_id: Optional column identifier (requires tab_id).
        update: If True, re-generate descriptions even if already present.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of columns with descriptions built or updated.

    Raises:
        ValueError: If database not found, col_id without tab_id, or invalid identifiers.
    """
    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")
    
    # Determine which columns to process
    columns_to_process: List[Tuple[str, str]] = []

    if tab_id is None:
        # Process all columns in all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            for column in table.columns.values():
                if column.disabled:
                    continue
                columns_to_process.append((table.tab_id, column.col_id))
    elif col_id is None:
        # Process all columns in specified table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        for column in table.columns.values():
            if column.disabled:
                continue
            columns_to_process.append((tab_id, column.col_id))
    else:
        # Process single column
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        column = table.columns.get(col_id)
        if column is None:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")
        if column.disabled:
            raise ValueError(f"Column '{col_id}' is disabled")
        columns_to_process.append((tab_id, col_id))
    
    if not columns_to_process:
        logger.info(f"No columns to process for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0
    
    # Pre-filter: determine which columns need description building
    columns_to_actually_process: List[Tuple[str, str, ColumnUKFT]] = []

    for t_id, c_id in columns_to_process:
        existing = kb.get_entity(tab_id=t_id, col_id=c_id, anchor=False)

        if existing is None:
            logger.warning(f"Column knowledge not found for {t_id}.{c_id}. Run 'rubiksql build column' first.")
            continue

        current_desc = existing.description
        if current_desc and not update:
            logger.debug(f"Skipping {t_id}.{c_id} - description already exists")
            continue

        columns_to_actually_process.append((t_id, c_id, existing))

    if not columns_to_actually_process:
        logger.info(f"All columns already have descriptions for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0
    
    # Define worker function for processing
    db_kl = kb.get_entity(anchor=False)
    tab_kl = kb.get_entity(tab_id=tab_id, anchor=False)
    def build_desc_worker(t_id: str, c_id: str, col_ukft: ColumnUKFT) -> Optional[ColumnUKFT]:
        """Build or update description for a single column."""
        try:
            col_desc = col_ukft.gen_desc(db_kl=db_kl, tab_kl=tab_kl)
            col_ukft.description = col_desc
            logger.debug(f"Built/Updated description for {t_id}.{c_id}")
            return (col_ukft.id, col_ukft)
        except Exception as e:
            logger.error(f"Failed to build description for {t_id}.{c_id}: {e}")
            return None
    
    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Process columns in parallel
    original_kids: List[int] = []
    updated_kls: List[ColumnUKFT] = []
    total = len(columns_to_actually_process)
    with Parallelized(
        func=build_desc_worker,
        args=[{"t_id": t_id, "c_id": c_id, "col_ukft": col_ukft} for t_id, c_id, col_ukft in columns_to_actually_process],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                logger.error(f"Error building description for column {t_id}.{c_id}: {error}")
                continue

            # Collect successful result
            if result is not None:
                original_kids.append(result[0])
                updated_kls.append(result[1])

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built/Updated description for {t_id}.{c_id}",
                        "step": "build_column_desc",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )
    
    # Batch upsert all updated knowledge
    if updated_kls:
        kb.batch_remove(original_kids, batch_size=batch_size)
        kb.batch_upsert(updated_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(updated_kls)} column descriptions")

        for ukft in updated_kls:
            t_id = ukft.get("tab_id")
            c_id = ukft.get("col_id")
            desc = ukft.description
            if t_id in db_info.tables and c_id in db_info.tables[t_id].columns:
                db_info.tables[t_id].columns[c_id].desc = desc
                logger.debug(f"Updated db_info description for {t_id}.{c_id}")
        RUBIK_DBM.save_db_info(db_id, db_info)
        logger.info(f"Saved updated db_info descriptions for {len(updated_kls)} columns")
    
    updated_count = len(updated_kls)
    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building descriptions for {updated_count} columns",
                "step": "build_column_desc",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )
    logger.info(f"Built/Updated descriptions for {updated_count} columns in db_id='{db_id}'")
    return updated_count


def build_table_synonyms(
    db_id: str,
    tab_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build or update table synonyms in the knowledge base.

    Uses LLM calls to generate or refine table synonyms based on column names.

    Supports hierarchical building:
    - If tab_id not specified: process all tables
    - If tab_id specified: process that single table

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        update: If True, re-generate synonyms even if already present.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of tables with synonyms built or updated.
    
    Raises:
        ValueError: If database not found or invalid table identifier.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which tables to process
    tables_to_process: List[str] = []

    if tab_id is None:
        # Process all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            tables_to_process.append(table.tab_id)
    else:
        # Process single table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        tables_to_process.append(tab_id)

    if not tables_to_process:
        logger.info(f"No tables to process for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Pre-filter: determine which tables need synonym building
    tables_to_actually_process: List[Tuple[str, TableUKFT]] = []

    for t_id in tables_to_process:
        existing = kb.get_entity(tab_id=t_id, anchor=False)
    
        if existing is None:
            logger.warning(f"Table knowledge not found for {t_id}. Run 'rubiksql build table' first.")
            continue

        current_synonyms = existing.synonyms
        if current_synonyms and not update:
            logger.debug(f"Skipping {t_id} - synonyms already exist")
            continue

        tables_to_actually_process.append((t_id, existing))

    if not tables_to_actually_process:
        logger.info(f"All tables already have synonyms for db_id='{db_id}', tab_id='{tab_id}'")
        return 0

    # Define worker function for processing
    db_kl = kb.get_entity(anchor=False)
    def build_synonyms_worker(t_id: str, tab_kl: TableUKFT) -> Optional[TableUKFT]:
        """Build or update synonyms for a single table."""
        try:
            tab_synonyms = tab_kl.gen_syns(db_kl=db_kl)
            tab_kl.synonyms = tab_synonyms
            logger.debug(f"Built/Updated synonyms for {t_id}")
            return (tab_kl.id, tab_kl)
        except Exception as e:  
            logger.error(f"Failed to build synonyms for {t_id}: {e}")
            return None
    
    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Process tables in parallel
    original_kids: List[int] = []
    updated_kls: List[TableUKFT] = []
    total = len(tables_to_actually_process)
    with Parallelized(
        func=build_synonyms_worker,
        args=[{"t_id": t_id, "tab_kl": tab_kl} for t_id, tab_kl in tables_to_actually_process],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                logger.error(f"Error building synonyms for table {t_id}: {error}")
                continue

            # Collect successful result
            if result is not None:
                original_kids.append(result[0])
                updated_kls.append(result[1])

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built synonyms for {t_id}",
                        "step": "build_table_synonyms",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )
    
    # Batch upsert all updated knowledge
    if updated_kls:
        kb.batch_remove(original_kids, batch_size=batch_size)
        kb.batch_upsert(updated_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(updated_kls)} table synonyms")
    
    built_count = len(updated_kls)
    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building {built_count} table synonyms",
                "step": "build_table_synonyms",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )
    
    logger.info(f"Built {built_count} table synonyms for db_id='{db_id}'")
    return built_count


def build_column_synonyms(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build or update column synonyms in the knowledge base.

    Uses LLM calls to generate or refine column synonyms based on column names.

    Supports hierarchical building:
    - If neither tab_id nor col_id specified: process all columns in all tables
    - If tab_id specified but not col_id: process all columns in that table
    - If both tab_id and col_id specified: process that single column

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        col_id: Optional column identifier (requires tab_id).
        update: If True, re-generate synonyms even if already present.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of columns with synonyms built or updated.

    Raises:
        ValueError: If database not found, col_id without tab_id, or invalid identifiers.
    """
    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")
    
    # Determine which columns to process
    columns_to_process: List[Tuple[str, str]] = []

    if tab_id is None:
        # Process all columns in all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            for column in table.columns.values():
                if column.disabled:
                    continue
                columns_to_process.append((table.tab_id, column.col_id))
    elif col_id is None:
        # Process all columns in specified table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        for column in table.columns.values():
            if column.disabled:
                continue
            columns_to_process.append((tab_id, column.col_id))
    else:
        # Process single column
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        column = table.columns.get(col_id)
        if column is None:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")
        if column.disabled:
            raise ValueError(f"Column '{col_id}' is disabled")
        columns_to_process.append((tab_id, col_id))
    
    if not columns_to_process:
        logger.info(f"No columns to process for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0
    
    # Pre-filter: determine which columns need synonym building
    columns_to_actually_process: List[Tuple[str, str, ColumnUKFT]] = []
    for t_id, c_id in columns_to_process:
        existing = kb.get_entity(tab_id=t_id, col_id=c_id, anchor=False)

        if existing is None:
            logger.warning(f"Column knowledge not found for {t_id}.{c_id}. Run 'rubiksql build column' first.")
            continue

        current_synonyms = existing.synonyms
        if current_synonyms and not update:
            logger.debug(f"Skipping {t_id}.{c_id} - synonyms already exist")
            continue

        columns_to_actually_process.append((t_id, c_id, existing))

    if not columns_to_actually_process:
        logger.info(f"All columns already have synonyms for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0
    
    # Define worker function for processing
    db_kl = kb.get_entity(anchor=False)
    tab_kl = kb.get_entity(tab_id=tab_id, anchor=False)
    def build_synonyms_worker(t_id: str, c_id: str, col_ukft: ColumnUKFT) -> Optional[ColumnUKFT]:
        """Build or update synonyms for a single column."""
        try:
            col_synonyms = col_ukft.gen_syns(db_kl=db_kl, tab_kl=tab_kl)
            col_ukft.synonyms = col_synonyms
            logger.debug(f"Built/Updated synonyms for {t_id}.{c_id}")
            return (col_ukft.id, col_ukft)
        except Exception as e:
            logger.error(f"Failed to build synonyms for {t_id}.{c_id}: {e}")
            return None
    
    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # Process columns in parallel
    original_kids: List[int] = []
    updated_kls: List[ColumnUKFT] = []
    total = len(columns_to_actually_process)
    with Parallelized(
        func=build_synonyms_worker,
        args=[{"t_id": t_id, "c_id": c_id, "col_ukft": col_ukft} for t_id, c_id, col_ukft in columns_to_actually_process],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                logger.error(f"Error building synonyms for column {t_id}.{c_id}: {error}")
                continue

            # Collect successful result
            if result is not None:
                original_kids.append(result[0])
                updated_kls.append(result[1])

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built synonyms for {t_id}.{c_id}",
                        "step": "build_column_synonyms",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )
    
    # Batch upsert all updated knowledge
    if updated_kls:
        kb.batch_remove(original_kids, batch_size=batch_size)
        kb.batch_upsert(updated_kls, batch_size=batch_size)
        logger.info(f"Batch upserted {len(updated_kls)} column synonyms")
    
    built_count = len(updated_kls)
    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed building {built_count} column synonyms",
                "step": "build_column_synonyms",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )
    
    logger.info(f"Built {built_count} column synonyms for db_id='{db_id}'")
    return built_count


def build_enum(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    update: bool = False,
    progress: Optional[Any] = None,
) -> int:
    """\
    Build EnumUKFT knowledge for enum values in columns.

    Only builds enums for columns where:
    1. enum_index_enabled is True (explicit), OR
    2. enum_index_enabled is None (default) AND datatype is TEXT or CATEGORICAL

    Supports hierarchical building:
    - If neither tab_id nor col_id specified: build all eligible columns in all tables
    - If tab_id specified but not col_id: build all eligible columns in that table
    - If both tab_id and col_id specified: build enums for that single column (if eligible)

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped building.
        col_id: Optional column identifier (requires tab_id).
        update: If True, rebuild even if enum knowledge already exists.
        progress: Optional progress tracker (RubikSQLRichProgress).

    Returns:
        Number of enums built.

    Raises:
        ValueError: If database not found, col_id without tab_id, or invalid identifiers.
    """
    from rubiksql.ukfs.enum_ukft import EnumUKFT
    from rubiksql.utils.db_utils import ColumnType

    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB and database info
    kb = load_kb(db_id)
    db = RUBIK_DBM.connect(db_id)
    db_info = kb.get_db_info()
    if db_info is None:
        raise ValueError(f"Database info not initialized for '{db_id}'. Run 'rubiksql info -n {db_id} -u' first.")

    # Determine which columns to process
    columns_to_process: List[Tuple[str, str]] = []

    if tab_id is None:
        # Process all columns in all enabled tables
        for table in db_info.tables.values():
            if table.disabled:
                continue
            for column in table.columns.values():
                if column.disabled:
                    continue
                columns_to_process.append((table.tab_id, column.col_id))
    elif col_id is None:
        # Process all columns in specified table
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        for column in table.columns.values():
            if column.disabled:
                continue
            columns_to_process.append((tab_id, column.col_id))
    else:
        # Process single column
        table = db_info.tables.get(tab_id)
        if table is None:
            raise ValueError(f"Table '{tab_id}' not found in database '{db_id}'")
        if table.disabled:
            raise ValueError(f"Table '{tab_id}' is disabled")
        column = table.columns.get(col_id)
        if column is None:
            raise ValueError(f"Column '{col_id}' not found in table '{tab_id}'")
        if column.disabled:
            raise ValueError(f"Column '{col_id}' is disabled")
        columns_to_process.append((tab_id, col_id))

    if not columns_to_process:
        logger.info(f"No columns to process for db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Pre-filter: determine which columns are eligible for enum building
    columns_to_build_enums: List[Tuple[str, str, ColumnUKFT]] = []

    for t_id, c_id in columns_to_process:
        # Get column UKFT
        col_ukft = kb.get_entity(tab_id=t_id, col_id=c_id, anchor=False)
        if col_ukft is None:
            logger.warning(f"Column knowledge not found for {t_id}.{c_id}. Run 'rubiksql build column' first.")
            continue

        # Get column metadata
        table = db_info.tables.get(t_id)
        column = table.columns.get(c_id)

        # Check enum eligibility
        enum_index_enabled = column.enum_index_enabled
        datatype = column.datatype_anno

        should_build_enums = False
        if enum_index_enabled is True:
            # Explicit enable
            should_build_enums = True
            logger.debug(f"{t_id}.{c_id}: enum_index explicitly enabled")
        elif enum_index_enabled is False:
            # Explicit disable
            should_build_enums = False
            logger.debug(f"{t_id}.{c_id}: enum_index explicitly disabled")
        else:  # enum_index_enabled is None
            # Default behavior: build for TEXT and CATEGORICAL
            if datatype:
                try:
                    datatype_enum = ColumnType(datatype)
                    if datatype_enum in (ColumnType.Text, ColumnType.Categorical):
                        should_build_enums = True
                        logger.debug(f"{t_id}.{c_id}: enum_index default enabled for {datatype_enum.name}")
                    else:
                        logger.debug(f"{t_id}.{c_id}: enum_index default disabled for {datatype_enum.name}")
                except ValueError:
                    logger.warning(f"{t_id}.{c_id}: invalid datatype '{datatype}'")
            else:
                logger.debug(f"{t_id}.{c_id}: no datatype annotated, skipping enum build")

        if should_build_enums:
            columns_to_build_enums.append((t_id, c_id, col_ukft))

    if not columns_to_build_enums:
        logger.info(f"No columns eligible for enum building in db_id='{db_id}', tab_id='{tab_id}', col_id='{col_id}'")
        return 0

    # Define worker function for building enums of a column
    def build_enum_worker(t_id: str, c_id: str, col_ukft: ColumnUKFT) -> List[EnumUKFT]:
        """Build EnumUKFTs for all distinct values in a column."""
        try:
            # Get distinct values from database
            # Use Database.col_freqs() which returns all distinct values with frequencies
            from ..api import load_db
            db = load_db(db_id)
            freqs_records = db.col_freqs(t_id, c_id)
            db.close()

            # Extract distinct values (exclude nulls)
            distinct_values = [record["col_enums"] for record in freqs_records if record["col_enums"] is not None]
            
            # Check if enums already exist (when not updating)
            if not update:
                # Query existing enums for this column
                existing_enums = kb.search(
                    engine="facet", 
                    type="db-enum",
                    tags={"DATABASE": db_id, "TABLE": t_id, "COLUMN": c_id}
                )
                if existing_enums:
                    logger.debug(f"Skipping {t_id}.{c_id} - {len(existing_enums)} enums already exist")
                    return []

            # Build EnumUKFTs
            enum_ukfts = []
            for enum_val in distinct_values:
                enum_ukft = EnumUKFT.from_enum(
                    db_id=db_id,
                    tab_id=t_id,
                    col_id=c_id,
                    enum_val=enum_val,
                    short_description="",
                    description="",
                    synonyms=None,
                ).signed(system=True, verified=True)
                enum_ukfts.append(enum_ukft)

            logger.debug(f"Built {len(enum_ukfts)} EnumUKFTs for {t_id}.{c_id}")
            return enum_ukfts
        except Exception as e:
            logger.error(f"Failed to build EnumUKFTs for {t_id}.{c_id}: {e}")
            return []

    # Get configuration
    num_threads = RUBIK_CM.get("klbase.build.num_threads", -1)
    batch_size = RUBIK_CM.get("klbase.batch_size", 256)

    # If updating, remove all existing enums for the columns
    if update:
        for t_id, c_id, _ in columns_to_build_enums:
            existing_enums = kb.search(
                engine="facet",
                type="db-enum",
                tags={"DATABASE": db_id, "TABLE": t_id, "COLUMN": c_id}
            )
            if existing_enums:
                existing_enum_ids = [e.id for e in existing_enums]
                kb.batch_remove(existing_enum_ids, batch_size=batch_size)
                logger.debug(f"Removed {len(existing_enum_ids)} existing enums for {t_id}.{c_id}")

    # Process columns in parallel
    all_enum_ukfts: List[EnumUKFT] = []
    total = len(columns_to_build_enums)

    with Parallelized(
        func=build_enum_worker,
        args=[{"t_id": t_id, "c_id": c_id, "col_ukft": col_ukft} for t_id, c_id, col_ukft in columns_to_build_enums],
        num_threads=num_threads,
    ) as tasks:
        # Gather results and update progress
        for idx, (kwargs, result, error) in enumerate(tasks):
            # Handle errors
            if error:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                logger.error(f"Error building enums for column {t_id}.{c_id}: {error}")
                continue

            # Collect successful results
            if result:
                all_enum_ukfts.extend(result)

            # Update progress
            if progress is not None:
                t_id = kwargs.get("t_id")
                c_id = kwargs.get("c_id")
                progress.emit(
                    {
                        "progress": (idx + 1) / total,
                        "message": f"Built {len(result)} enums for {t_id}.{c_id}",
                        "step": "build_enum",
                        "step_current": idx + 1,
                        "step_total": total,
                        "status": "building",
                    }
                )

    # Batch upsert all enum knowledge
    if all_enum_ukfts:
        kb.batch_upsert(all_enum_ukfts, batch_size=batch_size)
        logger.info(f"Batch upserted {len(all_enum_ukfts)} enum UKFTs")

    enum_count = len(all_enum_ukfts)

    # Final progress update
    if progress is not None:
        progress.emit(
            {
                "progress": 1.0,
                "message": f"Completed enum building for {total} columns",
                "step": "build_enum",
                "step_current": total,
                "step_total": total,
                "status": "completed",
            }
        )

    logger.info(f"Built {enum_count} enums for {total} columns in db_id='{db_id}'")
    return enum_count


def search_knowledge(
    db_id: str,
    tab_id: Optional[str] = None,
    col_id: Optional[str] = None,
    enum_val: Optional[Any] = None,
    mode: str = "facet",
) -> Optional[str]:
    """\
    Search for knowledge in the database using hierarchical scope.

    Currently supports "facet" mode which uses get_entity() to retrieve
    database/table/column/enum knowledge based on the hierarchy.

    Hierarchy:
    - db_id only: Retrieve database knowledge
    - db_id + tab_id: Retrieve table knowledge
    - db_id + tab_id + col_id: Retrieve column knowledge
    - db_id + tab_id + col_id + enum_val: Retrieve enum knowledge

    Args:
        db_id: Database identifier.
        tab_id: Optional table identifier for scoped search.
        col_id: Optional column identifier (requires tab_id).
        enum_val: Optional enum value (requires tab_id and col_id).
        mode: Search mode. Currently only "facet" is supported.

    Returns:
        String representation of the knowledge object, or None if not found.

    Raises:
        ValueError: If database not found, invalid hierarchy, or unsupported mode.
    """
    if col_id is not None and tab_id is None:
        raise ValueError("col_id requires tab_id to be specified")
    
    if enum_val is not None and (tab_id is None or col_id is None):
        raise ValueError("enum_val requires both tab_id and col_id to be specified")

    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    if mode != "facet":
        raise ValueError(f"Unsupported search mode: '{mode}'. Only 'facet' is currently supported for now.")

    # Load KB
    kb = load_kb(db_id)

    # If enum_val is provided, search for enum UKFT
    if enum_val is not None:
        from rubiksql.ukfs.enum_ukft import EnumUKFT
        from ahvn.utils.basic.str_utils import value_repr
        
        # Compose enum name like EnumUKFT.from_enum does
        full_name = f"{value_repr(tab_id)}.{value_repr(col_id)}={value_repr(enum_val)}"
        
        # Search by name
        results = kb.search(engine="facet", name=full_name)
        if results:
            return str(results[0])
        return None

    # Use get_entity to retrieve knowledge
    kl = kb.get_entity(tab_id=tab_id, col_id=col_id, anchor=False)

    if kl is None:
        return None

    # Return string representation
    return str(kl)


def upsert_skill(
    db_id: str,
    path: str,
    name: str,
    description: Optional[str] = None,
    update: bool = False,
    **kwargs,
) -> int:
    """\
    Upsert a skill UKFT into the knowledge base.

    Args:
        db_id: Database identifier.
        path: Skill creator path.
        name: Skill name.
        description: Optional skill description.
        update: If True, update existing skill if found.
        progress: Optional progress tracker (RubikSQLRichProgress).
        kwargs: Additional arguments for skill creation.

    Returns:
        Number of skills upserted (0 or 1).

    Raises:
        ValueError: If database not found.
        FileNotFoundError: If SKILL.md is not found in the directory.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB
    kb = load_kb(db_id)

    # Load SkillUKFT from path
    from ahvn.ukf.templates.basic import SkillUKFT
    
    role = kwargs.get("role", "")
    skill_ukft = SkillUKFT.from_path(path=path, name=name, description=description).signed(system=(role=="system"))
    name = skill_ukft.name

    # TODO: update kb.search with below
    # existing = kb.search(db_id=db_id, kl_id=name)
    existing_skills = list(kb.search(engine="skills", query=name, topk=1))
    skill_actually_to_update = []
    if existing_skills:
        skill_kl = existing_skills[0]["kl"]
        if skill_kl.name == name:
            if not update:
                logger.info(f"Skill '{name}' already exists in database '{db_id}', skipping upsert")
                return 0           
            if skill_kl.is_inactive:
                raise ValueError(f"Skill '{name}' is inactive in database '{db_id}'")
            if skill_kl.owner == "system":
                raise ValueError(f"Cannot upsert skill with name '{name}' because a system skill with that name already exists")
            
            skill_actually_to_update.append(skill_kl)

    if skill_actually_to_update:
        kb.batch_remove(skill_actually_to_update)
    kb.upsert(skill_ukft)
    logger.info(f"Upserted skill '{name}' into database '{db_id}'")

    return 1


def remove_skill(
    db_id: str,
    name: str,
) -> int:
    """\
    Remove a skill UKFT from the knowledge base.

    Args:
        db_id: Database identifier.
        name: Skill name.

    Returns:
        Number of skills removed (0 or 1).
    
    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB
    kb = load_kb(db_id)
    
    # Search for existing skill
    # TODO: update kb.search with below
    # existing = kb.search(db_id=db_id, kl_id=name)
    existing_skills = list(kb.search(engine="skills", query=name, topk=1))
    skill_actually_to_remove = []
    if existing_skills:
        skill_kl = existing_skills[0]["kl"]
        if skill_kl.name == name:
            if skill_kl.is_inactive:
                raise ValueError(f"Skill '{name}' is inactive in database '{db_id}'")
            if skill_kl.owner == "system":
                raise ValueError(f"Cannot remove skill with name '{name}' because it is a system skill")
            skill_actually_to_remove.append(skill_kl)

    if not skill_actually_to_remove:
        logger.info(f"Skill '{name}' does not exist in database '{db_id}', nothing to remove")
        return 0

    # Remove skill
    remove_count = len(skill_actually_to_remove)
    kb.batch_remove(skill_actually_to_remove)
    logger.info(f"Removed skill '{name}' from database '{db_id}'")

    return remove_count


def enable_skill(
    db_id: str,
    name: str,
) -> int:
    """\
    Enable a skill UKFT in the knowledge base.

    Args:
        db_id: Database identifier.
        name: Skill name.   
    
    Returns:
        Number of skills enabled (0 or 1).
    
    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB
    kb = load_kb(db_id)

    # Search for existing skill
    # TODO: update kb.search with below
    # existing = kb.search(db_id=db_id, kl_id=name)
    existing_skills = list(kb.search(engine="skills", query=name, topk=1))
    skill_ids_to_enable = []
    updated_skills = []
    if existing_skills:
        skill_kl = existing_skills[0]["kl"]
        if skill_kl.name == name and skill_kl.is_inactive:
            skill_ids_to_enable.append(skill_kl.id)
            skill_kl.set_active()
            updated_skills.append(skill_kl)

    if not skill_ids_to_enable:
        logger.info(f"Skill '{name}' is already active or not existed in database '{db_id}', nothing to enable")
        return 0

    # Enable skill
    enable_count = len(skill_ids_to_enable)
    kb.batch_remove(skill_ids_to_enable)
    kb.batch_upsert(updated_skills)
    logger.info(f"Enabled skill '{name}' in database '{db_id}'")

    return enable_count


def disable_skill(
    db_id: str,
    name: str,
) -> int:
    """\
    Disable a skill UKFT in the knowledge base.

    Args:
        db_id: Database identifier.
        name: Skill name.
    
    Returns:
        Number of skills disabled (0 or 1).

    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB
    kb = load_kb(db_id)

    # Search for existing skill
    # TODO: update kb.search with below
    # existing = kb.search(db_id=db_id, kl_id=name)
    existing_skills = list(kb.search(engine="skills", query=name, topk=1))
    skill_ids_to_disable = []
    updated_skills = []
    if existing_skills:
        skill_kl = existing_skills[0]["kl"]
        if skill_kl.name == name and skill_kl.is_active:
            skill_ids_to_disable.append(skill_kl.id)
            skill_kl.set_inactive()
            updated_skills.append(skill_kl)
    
    if not skill_ids_to_disable:
        logger.info(f"Skill '{name}' is already inactive or not existed in database '{db_id}', nothing to disable")
        return 0
    
    # Disable skill
    disable_count = len(skill_ids_to_disable)
    kb.batch_remove(skill_ids_to_disable)
    kb.batch_upsert(updated_skills)
    logger.info(f"Disabled skill '{name}' in database '{db_id}'")

    return disable_count


def list_skills(
    db_id: str,
) -> List[str]:
    """\
    List all skill UKFTs in the knowledge base.

    Args:
        db_id: Database identifier.

    Returns:
        List of string representations of skill UKFTs.

    Raises:
        ValueError: If database not found.
    """
    if not RUBIK_DBM.db_exists(db_id):
        raise ValueError(f"Database '{db_id}' not registered")

    # Load KB
    kb = load_kb(db_id)

    # Search for all skills
    skills = list(str(r["kl"]) for r in kb.search(engine="skills", query="", topk=100000))

    return skills