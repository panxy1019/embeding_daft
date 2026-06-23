# RubikSQL Development Instructions

## Background

RubikSQL is a general-purpose instance-optimized NL2SQL solution built on top of AgentHeaven. This is the **private development repository** for the core Python library - never make changes to the public `RubikSQL` repository.

### Project Ecosystem
- **AgentHeaven-dev**: Foundation framework providing knowledge management and agent capabilities
- **RubikSQL-gui**: Desktop GUI application that uses this package as its backend
- **RubikBench-dev**: Benchmarking suite for evaluating NL2SQL models (uses this for evaluation)
- **RubikSQL-paper**: Research paper describing the RubikSQL approach

## General Instructions

Always follow this workflow (unless explicitly asked by the user with "quick task"):
1. **Plan**: Start by thinking through the problem. Review the codebase for relevant files. Check `__tasks__/activities.md` for recent activities. Then create/clear `__tasks__/todo.md` and write a detailed plan in it, with a list of items to complete.
2. **Verification**: Wait for me to approve the plan before you begin.
3. **Execution**: Work through the todo list, checking off items as you complete them.
4. **Communication**: For every change you make, provide a concise, high-level explanation of what was modified.
5. **Simplicity**: Prioritize simplicity. Each change should be as small as possible, impacting minimal code. Avoid large or complex modifications.
6. **Review**: Once finished, add a summary of your changes to a new "Review" section in `__tasks__/todo.md`.
7. **Logging**: When explicitly asked to "commit", summarize the `__tasks__/todo.md` changes into `__tasks__/activities.md`, and delete `__tasks__/todo.md`. When explicitly asked to "compress", and if it is too long, summarize the `__tasks__/activities.md` changes by removing old entries.

## Environment & Setup

**Use the `rubiksql` conda environment**
```bash
conda activate rubiksql
```

## Project-Specific Instructions

### Utilities Preference
**IMPORTANT**: Use AgentHeaven utilities instead of system ones:

```python
# Instead of: import json, os, pathlib
from ahvn.utils import json_utils, file_utils
from rubiksql.utils import sql_utils, schema_utils

# For AgentHeaven features
from ahvn.klbase import KLBase
from ahvn.llm import LLMClient
from ahvn.ukf import UKFBase
```

### Database Operations
- **CRITICAL**: Use SQLAlchemy APIs for all database operations
- **NEVER** embed SQL strings directly in code
- Use the provided SQL utilities in `rubiksql/utils/sql_utils.py`
- Support multiple database backends (SQLite, PostgreSQL, MySQL, DuckDB, MSSQL)

### NL2SQL Architecture
When working with RubikSQL components:

1. **Agent System** (`rubiksql/agent.py`) - Main agent implementation using AgentHeaven
2. **Knowledge Base** (`rubiksql/klbase.py`) - Domain-specific knowledge base for SQL
3. **Tools** (`rubiksql/tools/`) - SQL execution, database introspection, query optimization
4. **UKFS** (`rubiksql/ukfs/`) - SQL-specific UKF implementations
5. **Models** (`rubiksql/models/`) - Pydantic models for data validation
6. **API Layer** (`rubiksql/routers/` and `rubiksql/services/`) - FastAPI endpoints and business logic

### Knowledge Management Integration
Leverage AgentHeaven's knowledge layer for storing:
- Database schemas
- Query patterns
- User feedback
- Performance metrics

### Code Organization
- Keep SQL-specific logic in `rubiksql/tools/`
- Use Pydantic models for all data structures
- Implement proper error handling for database operations
- Follow AgentHeaven's patterns for agent development

## Development Commands

### Installation
```bash
# Install in development mode
pip install -e .

# Install with dependencies
pip install -e ".[dev]"
```

### Running Tests
```bash
# Run test suite
python -m pytest tests/

# Run with coverage
python -m pytest --cov=rubiksql tests/
```

### Running Application
```bash
# Start FastAPI server
uvicorn main:app --reload --port 43252
```

### Running Benchmarks
```bash
# Run BIRD benchmark
python -m rubiksql.benchmarks.bird --data-dir data/BirdSQL

# Run KaggleDBQA benchmark
python -m rubiksql.benchmarks.kaggle --data-dir data/KaggleDBQA
```

## Performance Standards

Current performance to maintain or improve:
- **BIRD Mini-Dev**: 75.9% EX (n=1), 77.3% EX (n=8) with gemini-2.5-flash
- **KaggleDBQA**: 54.1% EX (n=1), 58.9% EX (n=8) with gemini-2.5-flash

## Key Features Implementation

### 1. Natural Language to SQL Conversion
- Use AgentHeaven's agent system with custom SQL tools
- Implement schema-aware query generation
- Support complex queries with joins and aggregations

### 2. Database Support
- SQLite (primary for development)
- PostgreSQL via asyncpg
- MySQL via pymysql
- DuckDB for analytical queries

### 3. Knowledge Base Integration
- Schema extraction and storage via AgentHeaven
- Query pattern learning
- Result feedback incorporation
- Instance optimization through experience

### 4. Error Handling and Recovery
- Query validation before execution
- Automatic query correction
- Fallback strategies for failed queries
- User-friendly error messages

## Testing Strategy

### Test Categories
1. **Unit Tests** (`tests/unit/`) - Individual component testing
2. **Integration Tests** (`tests/integration/`) - End-to-end NL2SQL pipeline
3. **Benchmarks** (`tests/benchmarks/`) - Performance on standard datasets
4. **Database Tests** (`tests/databases/`) - Multi-database compatibility

## Data Management

### Supported Datasets
- **BIRD** - Text-to-SQL benchmark
- **KaggleDBQA** - Real-world database queries
- **Spider** - Complex query dataset
- Custom datasets in JSON format

### Data Storage
```python
# Use ahvn file utilities for data I/O
from ahvn.utils.file_utils import read_json, write_json

# Store in AgentHeaven's knowledge base
from rubiksql.klbase import RubikSQLKLBase
kb = RubikSQLKLBase()
kb.store_schema(database_schema)
kb.store_query_pattern(nl_query, sql_query)
```

## Research Integration

This implementation is based on the research described in "Rubik: Bridging the NL2SQL Research-to-Production Gap via Lifelong Learning Agentic Knowledge Base".

Key innovations to maintain:
- Lifelong learning through knowledge accumulation
- Instance-specific optimization
- Agentic knowledge base for SQL generation
- Bridging research-to-production gap

## Repository Boundaries

- **NEVER** touch the public `RubikSQL` repository
- **ALWAYS** use AgentHeaven utilities for common operations
- **NO** embedded SQL strings - use SQLAlchemy
- **ALL** database operations must be error-handled
- **MUST** support multiple database backends
- **LEVERAGE** AgentHeaven's knowledge management for learning