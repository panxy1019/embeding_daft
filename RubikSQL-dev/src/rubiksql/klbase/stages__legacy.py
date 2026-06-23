"""\
Build stage definitions and utilities for RubikSQLKLBase.

Defines the build stages, their weights, order, and helper functions.
"""

__all__ = [
    "BuildStage",
    "STAGE_ORDER",
    "STAGE_WEIGHTS",
    "list_stages",
    "get_stage_weight",
    "get_normalized_weights",
    "get_accumulated_weights",
]

from enum import Enum
from typing import Dict, List


class BuildStage(str, Enum):
    """\
    Enumeration of knowledge base build stages.

    Each stage represents a distinct phase in the KB build process.
    Stages are ordered and weighted for progress calculation.
    """

    COUNT = "count"
    DATABASE = "database"
    TABLES = "tables"
    COLUMNS = "columns"
    ENUMS = "enums"
    DESCRIPTIONS = "descriptions"
    SYNONYMS = "synonyms"
    CLEAR = "clear"
    UPSERT = "upsert"
    DAAC = "daac"

    def __str__(self) -> str:
        return self.value


# Stage execution order
STAGE_ORDER: List[BuildStage] = [
    BuildStage.COUNT,
    BuildStage.DATABASE,
    BuildStage.TABLES,
    BuildStage.COLUMNS,
    BuildStage.ENUMS,
    BuildStage.DESCRIPTIONS,
    BuildStage.SYNONYMS,
    BuildStage.CLEAR,
    BuildStage.UPSERT,
    BuildStage.DAAC,
]

# Raw stage weights (relative importance for progress)
STAGE_WEIGHTS: Dict[BuildStage, float] = {
    BuildStage.COUNT: 2,
    BuildStage.DATABASE: 1,
    BuildStage.TABLES: 2,
    BuildStage.COLUMNS: 20,
    BuildStage.ENUMS: 30,
    BuildStage.DESCRIPTIONS: 50,
    BuildStage.SYNONYMS: 50,
    BuildStage.CLEAR: 5,
    BuildStage.UPSERT: 40,
    BuildStage.DAAC: 15,
}


def list_stages() -> List[str]:
    """\
    List all build stage names in execution order.

    Returns:
        List of stage name strings.
    """
    return [str(stage) for stage in STAGE_ORDER]


def get_stage_weight(stage: BuildStage) -> float:
    """\
    Get the raw weight for a stage.

    Args:
        stage: The build stage.

    Returns:
        Raw weight value.
    """
    return STAGE_WEIGHTS.get(stage, 0.0)


def get_normalized_weights(stages: List[BuildStage] = None) -> Dict[str, float]:
    """\
    Get normalized stage weights (summing to 1.0).

    Args:
        stages: Optional list of stages to include. If None, uses all stages.

    Returns:
        Dict mapping stage name to normalized weight.
    """
    if stages is None:
        stages = STAGE_ORDER

    weights = {str(s): STAGE_WEIGHTS.get(s, 0.0) for s in stages}
    total = sum(weights.values())
    if total <= 0:
        return {k: 0.0 for k in weights}
    return {k: v / total for k, v in weights.items()}


def get_accumulated_weights(stages: List[BuildStage] = None) -> Dict[str, float]:
    """\
    Calculate accumulated weights for each stage (cumulative sum before stage).

    Args:
        stages: Optional list of stages to include. If None, uses all stages.

    Returns:
        Dict mapping stage name to accumulated weight before that stage.

    Example:
        If normalized weights are {"a": 0.2, "b": 0.3, "c": 0.5},
        accumulated weights are {"a": 0.0, "b": 0.2, "c": 0.5}.
    """
    normalized = get_normalized_weights(stages)
    if stages is None:
        stages = STAGE_ORDER

    accum = {}
    cumsum = 0.0
    for stage in stages:
        key = str(stage)
        if key in normalized:
            accum[key] = cumsum
            cumsum += normalized[key]
    return accum


def calculate_stage_progress(
    stage: BuildStage,
    stage_idx: int,
    stage_total: int,
    stages: List[BuildStage] = None,
) -> float:
    """\
    Calculate overall progress for a point within a stage.

    Args:
        stage: Current build stage.
        stage_idx: Current item index within stage (0-based).
        stage_total: Total items in stage.
        stages: List of stages being run (for weight calculation).

    Returns:
        Overall progress as float between 0.0 and 1.0.
    """
    normalized = get_normalized_weights(stages)
    accumulated = get_accumulated_weights(stages)

    key = str(stage)
    base = accumulated.get(key, 0.0)
    weight = normalized.get(key, 0.0)

    if stage_total <= 0:
        return base

    return base + weight * (stage_idx / stage_total)
