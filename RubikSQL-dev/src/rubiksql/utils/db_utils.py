from enum import Enum
from typing import Dict, Any, List, Optional, Union


class ColumnType(Enum):
    LongText = "LONGTEXT"
    DateTime = "DATETIME"
    Identifier = "IDENTIFIER"
    Categorical = "CATEGORICAL"
    Integer = "INTEGER"
    Float = "FLOAT"
    Text = "TEXT"
    Unknown = "UNKNOWN"
