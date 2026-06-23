__all__ = [
    "ColumnUKFT",
    "col_info_detail_composer",
    "col_info_brief_composer",
    "col_ac_searched_composer",
]

from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from ahvn.utils.db import Database
from ahvn.utils.basic.str_utils import omission_list, value_repr, truncate, indent
from ahvn.utils.basic.misc_utils import counter_percentiles
from ahvn.utils.exts.autotask import autotask, AutoFuncError
from ahvn.ukf import ptags, gtags, register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT

from ..utils.config_utils import RUBIK_CM, rpj
from ..utils.db_utils import ColumnType
from ..resources.rubik_kb import RUBIK_KB

from typing import Set, Optional, Union, List, Tuple, Dict, Any, ClassVar
from datetime import datetime
from copy import deepcopy
import re

DEFAULT_NULL_CANDIDATES = [
    None,
    "",
    "NULL",
    "null",
    "NaN",
    "#N/A",
    "N/A",
    "#DIV/0!",
    "-",
]


def sample_topbot_enums(freqs: Dict[Any, int], topk: Optional[int] = None, bottomk: Optional[int] = None, cutoff: Optional[int] = None) -> Dict[str, Any]:
    """\
    Truncate enum storage to top-k and bottom-k values with repr length limits.

    Args:
        freqs: Dictionary mapping values to their frequencies
        topk: Number of top frequent values to store. Defaults to config value.
        bottomk: Number of bottom frequent values to store. Defaults to config value.
        cutoff: Maximum length of value representation to store. Defaults to config value.

    Returns:
        Dictionary with 'top_enums' and 'bot_enums' keys
    """
    if not freqs:
        return {"top_enums": {}, "bot_enums": {}}

    # Get cutoff from config
    topk = topk if topk is not None else RUBIK_CM.get("ukfts.col_ukft.enum_sample_top", 20)
    bottomk = bottomk if bottomk is not None else RUBIK_CM.get("ukfts.col_ukft.enum_sample_bottom", 20)
    cutoff = cutoff if cutoff is not None else RUBIK_CM.get("ukfts.col_ukft.enum_value_cutoff", 128)
    sorted_items = sorted(freqs.items(), key=lambda x: x[1], reverse=True)
    top_enums = {value_repr(k, cutoff=cutoff): int(v) for k, v in sorted_items[:topk]}
    bot_enums = {value_repr(k, cutoff=cutoff): int(v) for k, v in sorted_items[-bottomk:]} if len(sorted_items) > topk else {}
    return {"top_enums": top_enums, "bot_enums": bot_enums}


def freq_dists(freqs: Dict[Any, int], intervals: Optional[Union[int, List[int]]] = None) -> Dict[str, int]:
    """\
    Compute frequency distribution based on cumulative mass intervals.

    Args:
        freqs: Dictionary mapping values to their frequencies
        intervals: List of cumulative percentage thresholds (e.g., [0, 5, 10, ..., 100]).
            If None, uses tools.enums_interval from config (default 20 means 5% intervals).

    Returns:
        Dictionary mapping interval labels to counts of distinct values in that range
    """
    if intervals is None:
        intervals = 20
    if isinstance(intervals, int):
        step = 100 // intervals
        intervals = set([0] + [step * i for i in range(1, intervals + 1)] + [100])
    intervals = sorted(intervals)
    if not freqs:
        return {}

    sorted_items = sorted(freqs.items(), key=lambda x: x[1], reverse=True)
    total, cumsum, cnt, idx, dists = sum(freqs.values()), 0, 0, 0, dict()

    for _, freq in sorted_items:
        cumsum += freq
        cnt += 1
        while idx < len(intervals) and (cumsum / total) * 100 >= intervals[idx]:
            dists[intervals[idx]] = cnt
            idx += 1
        if idx >= len(intervals):
            break
    return dists


def col_info_detail_composer(kl, **kwargs):
    """\
    Compose a brief description of column information.

    Generates a text description of a database column, including its data type,
    null count, and distinct value count. The level of detail is controlled by
    the mode parameter.

    Recommended Knowledge Types:
        ColumnUKFT

    Args:
        kl (BaseUKF): Knowledge object containing column metadata.
        mode (Literal["detail", "brief"]): Output mode. Defaults to "detail".
            - "detail": Multi-line detailed description with full statistics.
            - "brief": Single-line concise summary with key statistics only.

    Returns:
        str: Formatted description of the column.
    """
    col_id = kl.get("col_id", "")
    tab_id = kl.get("tab_id", "")
    datatype = kl.get("datatype", "UNKNOWN")
    datatype_enum = ColumnType.Unknown if datatype is None else ColumnType(datatype)
    n_rows = kl.get("# rows", 0)
    n_distincts = kl.get("# distincts", 0)
    n_nulls = kl.get("# null", 0)
    # Use stored top_enums and bot_enums for display
    top_enums = {eval(k): v for k, v in kl.get("top_enums", {}).items()}
    bot_enums = {eval(k): v for k, v in kl.get("bot_enums", {}).items()}
    enums = top_enums | bot_enums  # Combine for display
    accum_enums, accum = [(k, v, 0) for k, v in enums.items()], 0
    for i, (k, v, _) in enumerate(accum_enums):
        accum += v
        accum_enums[i] = (k, v, accum)
    description = kl.description
    short_description = kl.short_description
    stats = kl.get("stats", {}) | {"synonyms": list(kl.synonyms - {col_id})}
    is_pk = kl.get("is_pk", False)
    fks = kl.get("fks", [])

    if datatype_enum in [
        ColumnType.Float,
        ColumnType.Integer,
    ]:
        stats["avg"] = f"{stats.get('avg', None):.2f}" if stats.get("avg", None) is not None else None
        stats["maj"] = f"{stats.get('maj', (None, 0))[0]} (freq={stats.get('maj', (None, 0))[1] * 100 / n_rows:.2f}%)"
        return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_info_detail.jinja").render(
            col_id=col_id,
            tab_id=tab_id,
            datatype=datatype_enum.name,
            description=description or short_description,
            n_rows=n_rows,
            n_distincts=n_distincts,
            n_nulls=n_nulls,
            accum_enums=list(),
            stats=stats,
            is_pk=is_pk,
            fks=fks,
        )
    if datatype_enum in [
        ColumnType.Text,
        ColumnType.LongText,
        ColumnType.Categorical,
    ]:
        enum_sample_top = RUBIK_CM.get("ukfts.col_ukft.detail.enum_sample_top", 5)
        enum_sample_bottom = RUBIK_CM.get("ukfts.col_ukft.detail.enum_sample_bottom", 5)
        return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_info_detail.jinja").render(
            col_id=col_id,
            tab_id=tab_id,
            datatype=datatype_enum.name,
            description=description or short_description,
            n_rows=n_rows,
            n_distincts=n_distincts,
            n_nulls=n_nulls,
            accum_enums=omission_list(accum_enums, top=enum_sample_top, bottom=enum_sample_bottom),
            stats=stats,
            is_pk=is_pk,
            fks=fks,
        )
    if datatype_enum in [
        ColumnType.DateTime,
        ColumnType.Identifier,
    ]:
        return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_info_detail.jinja").render(
            col_id=col_id,
            tab_id=tab_id,
            datatype=datatype_enum.name,
            description=description or short_description,
            n_rows=n_rows,
            n_distincts=n_distincts,
            n_nulls=n_nulls,
            accum_enums=list(),
            stats=stats,
            is_pk=is_pk,
            fks=fks,
        )
    if datatype_enum == ColumnType.Unknown:
        return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_info_detail.jinja").render(
            col_id=col_id,
            tab_id=tab_id,
            datatype=datatype_enum.name,
            description=description or short_description,
            n_rows=n_rows,
            n_distincts=n_distincts,
            n_nulls=n_nulls,
            accum_enums=list(),
            stats=stats,
            is_pk=is_pk,
            fks=fks,
        )
    raise ValueError(f"Unsupported datatype for col_info_detail_composer: {datatype_enum}")


def col_info_brief_composer(kl, **kwargs):
    """\
    Compose a single-line brief description of column information.

    Generates a concise text description of a database column in a single line,
    including its full name, data type, null status, description, and sampled enum values.

    Recommended Knowledge Types:
        ColumnUKFT

    Args:
        kl (BaseUKF): Knowledge object containing column metadata.
        samples (Union[int, Tuple[int, int], List[Any]]): Number of enum samples to include.
            If an integer is provided, that many samples will be taken from both the start and end
            of the enum list. If a tuple is provided, it specifies the number of samples from
            the start and end respectively. If a list is provided, those specific enum values will
            be used directly.

    Returns:
        str: Formatted single-line description of the column.
    """
    col_id = kl.get("col_id", "")
    tab_id = kl.get("tab_id", "")
    datatype = kl.get("datatype", "UNKNOWN")
    datatype_enum = ColumnType.Unknown if datatype is None else ColumnType(datatype)
    n_nulls = kl.get("# null", 0)
    top_enums = {eval(k): v for k, v in kl.get("top_enums", {}).items()}
    bot_enums = {eval(k): v for k, v in kl.get("bot_enums", {}).items()}
    enums = top_enums | bot_enums  # Combine for display
    enum_sample_top = RUBIK_CM.get("ukfts.col_ukft.brief.enum_sample_top", 2)
    enum_sample_bottom = RUBIK_CM.get("ukfts.col_ukft.brief.enum_sample_bottom", 1)
    samples = deepcopy(kwargs.get("samples", (enum_sample_top, enum_sample_bottom)))
    if isinstance(samples, int):
        samples = (samples, samples)
    if isinstance(samples, tuple):
        samples = list(enums.keys())[: samples[0]] + list(enums.keys())[-samples[1] :] if len(enums) > sum(samples) else list(enums.keys())
    description = kl.description
    short_description = kl.short_description
    short_stat = kl.get("short_stat", {})

    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_info_brief.jinja").render(
        col_id=col_id,
        tab_id=tab_id,
        datatype=datatype_enum.name,
        description=description or short_description,
        n_nulls=n_nulls,
        enums=samples,
        short_stat=short_stat,
    )


def col_ac_searched_composer(kl, **kwargs):
    """\
    Compose a string describing the AC search match for a column.

    Uses the DAACKLEngine's search result stored in kl.metadata["search"]
    to compose strings like: "Knowledge that might be related to keyword '...' in query, Column: ..."

    Recommended Knowledge Types:
        ColumnUKFT

    Args:
        kl (BaseUKF): Knowledge object containing column metadata and search results.

    Returns:
        str: Formatted string describing the search match.
    """
    from ahvn.utils.basic.config_utils import dget

    search_strs = "/".join(dget(kl.metadata, "search.returns.strs", list()))
    return PromptUKFT.from_path(rpj("& prompts/db/"), default_entry="col_ac_searched.jinja").render(
        search_strs=search_strs,
        knowledge=col_info_brief_composer(kl, **kwargs),
    )


def parse_datetime_format(
    date_strs: List[str],
    datetime_sample_top: Optional[int] = None,
    datetime_sample_bottom: Optional[int] = None,
) -> Optional[str]:
    """\
    Given a list of date strings, identify the common datetime format.

    Args:
        date_strs (List[str]): List of date strings sampled from a database column.
        datetime_sample_top (Optional[int]): Number of samples to take from the start of the list. Defaults to config value.
        datetime_sample_bottom (Optional[int]): Number of samples to take from the end of the list. Defaults to config value.

    Returns:
        Optional[str]: The Python strptime format string if all date strings share the same format, else None.
    """
    if not date_strs:
        return None

    datetime_parser = autotask(
        prompt=RUBIK_KB.get_prompt("datetime_parser"),
        llm_args={"preset": "tiny"},
    )

    datetime_sample_top = datetime_sample_top if datetime_sample_top is not None else RUBIK_CM.get("ukfts.col_ukft.parse_datetime_format.enum_sample_top", 3)
    datetime_sample_bottom = (
        datetime_sample_bottom if datetime_sample_bottom is not None else RUBIK_CM.get("ukfts.col_ukft.parse_datetime_format.enum_sample_bottom", 3)
    )
    sampled_date_strs = [sample for sample in (date_strs[:3] + date_strs[-3:] if len(date_strs) > 6 else date_strs)]

    try:
        format_str = datetime_parser(date_strs=sampled_date_strs)
        if (format_str is not None) and not isinstance(format_str, str):
            raise AutoFuncError(f"Generated datetime format is not a string or None, but {type(format_str)}: {format_str}")
    except AutoFuncError as e:
        logger.warning(f"Failed to parse datetime format from datetime string samples {sampled_date_strs}. {e}")
        return None

    if format_str is None:
        logger.debug(f"Generated datetime format is None. The column is likely not datetime or has mixed formats: {sampled_date_strs}.")
        return None

    for date_str in date_strs:
        try:
            datetime.strptime(date_str, format_str)
        except Exception as e:
            logger.warning(f"Generated datetime format string '{format_str}' cannot parse all date strings. {e}")
            return None
    return format_str


@register_ukft
class ColumnUKFT(KnowledgeUKFT):
    """\
    Database table columns.

    UKF Type: knowledge >> column
    Recommended Tags:
        - DATABASE
        - TABLE
        - COLUMN

    Recommended Relations:
        - ColumnUKFT -- in_table -> TableUKFT
        - TableUKFT -- has_column -> ColumnUKFT
        - ColumnUKFT -- in_database -> DatabaseUKFT
        - ColumnUKFT -- in_taxonomy[idx] -> TaxonomyUKFT
        - TaxonomyUKFT -- has_column[idx] -> ColumnUKFT

    Recommended Components of `content_resources`:
        - db_id (str): Database identifier
        - tab_id (str): Table identifier
        - col_id (str): Column identifier
        - datatype_orig (str): Original database type string
        - datatype (ColumnType): Inferred column type
        - # rows (int): Total row count
        - # distincts (int): Number of distinct non-null values
        - # null (int): Number of null-like values
        - enums (Dict[str, int]): Value frequencies (repr(val) -> count)
        - is_pk (bool): True if this column is a single-column primary key
        - fks (List[Dict[str, str]]): Foreign key references, each with tab_ref, col_ref, name

    Recommended Composers:
        default: col_composer (mode="detail") - Detailed description with statistics and value distribution
        detail: col_composer (mode="detail") - Detailed description with statistics and value distribution
        brief: col_composer (mode="brief") - Concise single-line summary
        auto: col_auto_composer - Type-aware description with intelligent statistics (percentiles for numeric, avg length for text)
    """

    type_default: ClassVar[str] = "db-column"

    @classmethod
    def from_col(
        cls,
        db_id: str,
        tab_id: str,
        col_id: str,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        datatype: Optional[Union[str, ColumnType]] = None,
        enum_index: Optional[bool] = None,
        synonyms: Optional[Set[str]] = None,
        null_candidates: Optional[Set[str]] = None,
        is_pk: bool = False,
        fks: Optional[List[Dict[str, str]]] = None,
    ) -> "ColumnUKFT":
        full_name = f"{value_repr(tab_id)}.{value_repr(col_id)}"
        short_description = "" if short_description is None else short_description
        description = "" if description is None else description
        from ..api import load_db

        db = load_db(db_id)
        row_count = db.row_count(tab_id)
        datatype_orig = str(db.col_type(tab_id, col_id))
        datatype_anno = str(datatype.value if isinstance(datatype, ColumnType) else datatype).upper() if datatype is not None else None
        datatype = datatype_anno or "UNKNOWN"
        freqs = {record["col_enums"]: record["freq"] for record in db.col_freqs(tab_id, col_id)}
        null_candidates = set(DEFAULT_NULL_CANDIDATES if null_candidates is None else null_candidates)
        non_null_freqs = {k: v for k, v in freqs.items() if (k not in null_candidates) and (k is not None)}
        fks = fks or []
        enums = sample_topbot_enums(non_null_freqs)
        intervals = RUBIK_CM.get("ukfts.col_ukft.enum_dists_interval", 20)
        dists = freq_dists(non_null_freqs, intervals=intervals)
        db.close()

        return cls(
            name=full_name,
            short_description=short_description,
            description=description,
            owner="admin",  # System-generated knowledge
            content_resources={
                "db_id": db_id,
                "tab_id": tab_id,
                "col_id": col_id,
                "datatype_orig": datatype_orig,
                "datatype_anno": datatype_anno,
                "datatype": datatype,
                "enum_index": enum_index,
                "# rows": row_count,
                "null_candidates": list(null_candidates),
                "# distincts": len(non_null_freqs),
                "# null": sum(freqs.values()) - sum(non_null_freqs.values()),
                "top_enums": enums["top_enums"],
                "bot_enums": enums["bot_enums"],
                "freq_dists": dists,
                "is_pk": is_pk,
                "fks": fks,
            },
            content_composers={
                "default": col_info_brief_composer,
                "detail": col_info_detail_composer,
                "brief": col_info_brief_composer,
                "ac": col_ac_searched_composer,
            },
            tags=ptags(
                DATABASE=db_id,
                TABLE=tab_id,
                COLUMN=col_id,
                DATATYPE=datatype,
            ),
            synonyms={col_id} | (synonyms or set()),
        )

    @property
    def db_id(self) -> str:
        return self.get("db_id", "")

    @property
    def tab_id(self) -> str:
        return self.get("tab_id", "")

    @property
    def col_id(self) -> str:
        return self.get("col_id", "")

    @property
    def datatype(self) -> ColumnType:
        return ColumnType(self.get("datatype", "UNKNOWN"))

    @property
    def is_pk(self) -> bool:
        """Whether this column is a single-column primary key."""
        return self.get("is_pk", False)

    @property
    def fks(self) -> List[Dict[str, str]]:
        """Foreign key references for this column. Each entry has tab_ref, col_ref, name."""
        return self.get("fks", [])

    def freqs(self, enum_val: Optional[Any] = None) -> Union[int, Dict[Any, int]]:
        """\
        Get the value frequency/frequencies for this column from the database.

        Args:
            enum_val (Optional[Any]): Specific enum value to get frequency for.
                If None, returns all frequencies.

        Returns:
            int: Frequency count if enum_val is specified.
            Dict[Any, int]: Dictionary mapping values to frequencies if enum_val is None.
                Includes key `None` for null-like values.
        """
        from ..api import load_db

        db = load_db(self.db_id)  # A one-time connection for frequency retrieval
        null_candidates = set(self.get("null_candidates", DEFAULT_NULL_CANDIDATES))
        freqs_all = {record["col_enums"]: record["freq"] for record in db.col_freqs(self.tab_id, self.col_id)}
        db.close()  # Close the one-time connection
        non_null_freqs = {k: v for k, v in freqs_all.items() if (k not in null_candidates) and (k is not None)}
        if enum_val is not None:
            if enum_val is None:
                return sum(freqs_all.values()) - sum(non_null_freqs.values())
            return non_null_freqs.get(enum_val, 0)
        return {None: sum(freqs_all.values()) - sum(non_null_freqs.values())} | non_null_freqs

    def type_annotated(
        self,
        datatype: Union[str, ColumnType],
        **kwargs,
    ) -> "ColumnUKFT":
        """\
        Return a new ColumnUKFT with the specified datatype annotation.

        Args:
            datatype (Union[str, ColumnType]): The datatype to annotate the column with.
            kwargs: Additional keyword arguments (currently unused).

        Returns:
            ColumnUKFT: A new ColumnUKFT instance with the updated datatype.
        """
        if isinstance(datatype, ColumnType):
            datatype = datatype.value
        else:
            datatype = str(datatype).upper()

        # Create updated content resources
        updated_content_resources = {
            **self.content_resources,
            "datatype": datatype,
        }

        # Compute stats based on datatype
        datatype_enum = ColumnType(datatype)
        if datatype_enum in [
            ColumnType.Text,
            ColumnType.LongText,
        ]:
            stats = dict()
            enum_freqs = self.freqs()  # Use self to avoid contamination
            enum_items = [(k, v) for k, v in enum_freqs.items() if k is not None]
            stats["min_length"] = min((len(str(k)) for k, _ in enum_items), default=None)
            stats["avg_length"] = int(round(sum(len(str(k)) * v for k, v in enum_items) / sum(v for _, v in enum_items))) if enum_items else None
            stats["max_length"] = max((len(str(k)) for k, _ in enum_items), default=None)
            short_stat = f"length_range=[{stats['min_length']}~{stats['max_length']}], avg_length={stats['avg_length']}"
            updated_content_resources |= {"stats": stats, "short_stat": short_stat}
        if datatype_enum in [
            ColumnType.Integer,
            ColumnType.Float,
        ]:
            stats = {}
            enum_freqs = self.freqs()
            if datatype_enum == ColumnType.Integer:
                enum_items = [(int(k), v) for k, v in enum_freqs.items() if k is not None]
            else:
                enum_items = [(float(k), v) for k, v in enum_freqs.items() if k is not None]

            cnt = sum(v for _, v in enum_items)
            stats["avg"] = sum(k * v for k, v in enum_items) / cnt if cnt > 0 else None
            stats["maj"] = next(iter(enum_items), (None, 0))
            percentiles = counter_percentiles(dict(enum_items), percentiles=[0, 25, 50, 75, 100])
            stats["min"] = percentiles[0]
            stats["p25"] = percentiles[25]
            stats["p50"] = percentiles[50]
            stats["p75"] = percentiles[75]
            stats["max"] = percentiles[100]
            if datatype_enum == ColumnType.Float:
                short_stat = f"range=[{float(stats['min']):.2f}~{float(stats['max']):.2f}], avg={float(stats['avg']):.2f}" if stats["avg"] is not None else ""
            else:
                short_stat = f"range=[{int(stats['min'])}~{int(stats['max'])}], avg={float(stats['avg']):.2f}" if stats["avg"] is not None else ""
            updated_content_resources |= {"stats": stats, "short_stat": short_stat}
        if datatype_enum in [
            ColumnType.DateTime,
        ]:
            stats = dict()
            enum_freqs = self.freqs()
            enum_vals = [k for k in enum_freqs.keys() if k is not None]

            if kwargs.get("format"):
                stats["format"] = kwargs["format"]
            else:
                stats["format"] = parse_datetime_format(date_strs=[str(e) for e in enum_vals])
            if stats["format"] is not None and enum_vals:
                stats["min_time"] = min(enum_vals, key=lambda e: datetime.strptime(str(e), stats["format"]), default=None)
                stats["max_time"] = max(enum_vals, key=lambda e: datetime.strptime(str(e), stats["format"]), default=None)
            else:
                stats["min_time"] = None
                stats["max_time"] = None
            short_stat = f"fmt='{stats['format']}'" if stats["format"] is not None else ""
            updated_content_resources |= {"stats": stats, "short_stat": short_stat}
        if datatype_enum in [ColumnType.Categorical]:
            stats = dict()
            stats["n_classes"] = self.get("# distincts", 0)
            short_stat = f"n_classes={stats['n_classes']}"
            updated_content_resources |= {"stats": stats, "short_stat": short_stat}
        if datatype_enum in [ColumnType.Identifier]:
            stats = dict()
            short_stat = None
            updated_content_resources |= {"stats": stats, "short_stat": short_stat}

        return self.clone(
            content_resources=updated_content_resources,
            tags=ptags(**gtags(self.tags) | {"DATATYPE": datatype}),
        )

    def type_deduction(
        self,
        overwrite: bool = False,
        categorical_max_cnt: Optional[int] = None,
        categorical_max_ratio: Optional[float] = None,
        longtext_min_max_len: Optional[int] = None,
        identifier_min_ratio: Optional[float] = None,
    ) -> Tuple[ColumnType, Dict]:
        """\
        Deduce the column datatype based on its enum values.

        Args:
            overwrite (bool): Whether to overwrite existing datatype annotation. Defaults to False.

        Returns:
            Tuple[ColumnType, Dict]: Deduced datatype and additional info.
        """
        categorical_max_cnt = categorical_max_cnt if categorical_max_cnt is not None else RUBIK_CM.get("ukfts.col_ukft.type_deduction.categorical_max_cnt", 64)
        categorical_max_ratio = (
            categorical_max_ratio if categorical_max_ratio is not None else RUBIK_CM.get("ukfts.col_ukft.type_deduction.categorical_max_ratio", 0.5)
        )
        longtext_min_max_len = (
            longtext_min_max_len if longtext_min_max_len is not None else RUBIK_CM.get("ukfts.col_ukft.type_deduction.longtext_min_max_len", 256)
        )
        identifier_min_ratio = (
            identifier_min_ratio if identifier_min_ratio is not None else RUBIK_CM.get("ukfts.col_ukft.type_deduction.identifier_min_ratio", 0.99)
        )

        datatype = self.get("datatype", "UNKNOWN")
        datatype_enum = ColumnType.Unknown if datatype is None else ColumnType(datatype)
        if datatype_enum != ColumnType.Unknown and not overwrite:
            return datatype_enum, dict()
        n_rows = self.get("# rows", 0)
        n_nulls = self.get("# null", 0)
        total = n_rows - n_nulls

        enum_freqs = self.freqs()
        enums = [k for k in enum_freqs.keys() if k is not None]

        if len(enums) > 0 and (max((len(str(k)) for k in enums), default=0) > longtext_min_max_len):
            return ColumnType.LongText, dict()
        if len(enums) > 0:
            datetime_format = parse_datetime_format(date_strs=[str(k) for k in enums])
            if datetime_format is not None:
                return ColumnType.DateTime, {"format": datetime_format}
        # strings like "+3", "1,000", "5.0", "023", "-0", "00" are not considered integers for conservative deduction
        is_integer_pattern = re.compile(r"^0$|^-?[1-9]\d*$")
        is_integer_type = all(is_integer_pattern.fullmatch(str(k)) for k in enums)
        is_float_pattern = re.compile(r"^(?!-0(?:\.0+)?$)-?(?:0|[1-9]\d*)(?:\.\d+)?$")
        is_float_type = all(is_float_pattern.fullmatch(str(k)) for k in enums)
        if len(enums) > 0 and (len(enums) >= total * identifier_min_ratio) and (not is_float_type):
            return ColumnType.Identifier, dict()
        if len(enums) > 0 and is_integer_type:
            return ColumnType.Integer, dict()
        if len(enums) > 0 and is_float_type:
            return ColumnType.Float, dict()
        if len(enums) > 0 and (len(enums) <= categorical_max_cnt) and (len(enums) <= categorical_max_ratio * total):
            return ColumnType.Categorical, dict()
        if len(enums) > 0 and all(isinstance(k, str) for k in enums):
            return ColumnType.Text, dict()
        return ColumnType.Unknown, dict()
        # TODO: type-deduction based on datatype_orig

    def type_deduced(self, overwrite: bool = False) -> "ColumnUKFT":
        """\
        Return a new ColumnUKFT with the deduced datatype annotation.

        Args:
            overwrite (bool): Whether to overwrite existing datatype annotation. Defaults to False.

        Returns:
            ColumnUKFT: A new ColumnUKFT instance with the updated datatype.
        """
        datatype_enum, stats = self.type_deduction(overwrite=overwrite)
        return self.type_annotated(datatype=datatype_enum, **stats)

    def gen_desc(self, db_kl=None, tab_kl=None, instructions: Optional[Union[str, List[str]]] = None, **kwargs) -> str:
        col_profile = self.text(composer="detail", **kwargs)
        db_profile = db_kl.text(composer="default") if db_kl else None
        tab_profile = tab_kl.text(composer="brief") if tab_kl else None
        descriptions = [
            f"Column Schema Information:\n{indent(col_profile)}",
            f"Schema Information of the Table that the Column belongs to:\n{indent(tab_profile)}" if tab_profile else None,
            f"Schema Information of the Database that the Column belongs to:\n{indent(db_profile)}" if db_profile else None,
        ]
        try:
            desc = autotask(
                prompt=RUBIK_KB.get_prompt("col_gen_desc"),
                descriptions=descriptions,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if not isinstance(desc, str):
                raise AutoFuncError(f"Generated description is not a string, but {type(desc)}: {desc}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate description for column {self.name}. {e}")
            desc = self.description or self.short_description or ""
        self.description = truncate(desc.strip(), cutoff=KnowledgeUKFT.schema()["description"].max_length())
        return self.description

    def gen_syns(
        self, db_kl=None, tab_kl=None, prompt: Optional[PromptUKFT] = None, instructions: Optional[Union[str, List[str]]] = None, **kwargs
    ) -> Set[str]:
        col_profile = self.text(composer="detail", **kwargs)
        db_profile = db_kl.text(composer="default") if db_kl else None
        tab_profile = tab_kl.text(composer="brief") if tab_kl else None
        try:
            descriptions = [
                f"Column Schema Information:\n{indent(col_profile)}",
                f"Schema Information of the Table that the Column belongs to:\n{indent(tab_profile)}" if tab_profile else None,
                f"Schema Information of the Database that the Column belongs to:\n{indent(db_profile)}" if db_profile else None,
            ]
            synonyms_list = autotask(
                prompt=prompt or RUBIK_KB.get_prompt("col_gen_syns"),
                descriptions=descriptions,
                instructions=instructions,
                lang=RUBIK_CM.get("core.lang", "en"),
                llm_args={"preset": "chat"},
            )()
            if isinstance(synonyms_list, str):
                synonyms_list = [synonyms_list]
            if not isinstance(synonyms_list, list):
                raise AutoFuncError(f"Generated synonyms is not a list, but {type(synonyms_list)}: {synonyms_list}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate synonyms for column {self.name}. {e}")
            synonyms_list = list()
        synonyms = set(syn.strip() for syn in synonyms_list if syn.strip())
        self.synonyms = self.synonyms.union(synonyms)
        return self.synonyms
