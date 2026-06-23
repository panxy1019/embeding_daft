# AgentHeaven Development Instructions

## Background

AgentHeaven is an open-source Python package that provides knowledge management utilities for modern LLM Agents, aiming to be a key infrastructure supporting AI agents. This is the **private development repository** - never make changes to the public `AgentHeaven` repository.

### Project Ecosystem
- **RubikSQL-dev**: Uses AgentHeaven as its foundation for NL2SQL functionality
- **RubikSQL-gui**: Desktop GUI that consumes both RubikSQL and AgentHeaven packages
- **AgentHeaven-docs**: Contains comprehensive bilingual documentation (English/Chinese)
- **RubikBench-dev**: Benchmarking suite for evaluating systems built on AgentHeaven

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

**CRITICAL: Always use the `rubik` conda environment**
```bash
conda activate rubik
```

Create environment if needed:
```bash
conda env create -f environment.yml
```

## Project-Specific Instructions

### Utilities Preference
**IMPORTANT**: Always use ahvn utilities instead of system ones:

```python
# Instead of: import json, os, pathlib, constants
from ahvn.utils import json_utils, file_utils, config_utils, constants

# Example usage:
from ahvn.utils.file_utils import read_json, write_json, ensure_dir
from ahvn.utils.json_utils import safe_loads, safe_dumps
from ahvn.utils.config_utils import get_config, set_config
from ahvn.utils import constants
```

### Code Quality & Standards
- Uphold **standard software engineering principles**. Focus on clean code, clear comments, and maintainable systems.
- **Resources**: Place all code, prompts, and configuration resources in `src/ahvn/resources`.
- **User Config**: The user config is at `~/.ahvn/config.yaml`. To reset it, run `bash secret/setup.bash`, this should be run whenever there are changes to the default config in `secret/default_config.yaml`.
- **Memory**: Current Focus, Recent Completed, Suspended Tasks are all documented in `__tasks__/activities.md`.
- **Documentation**: Make sure all sections of all levels are numbered (except root). Sections should end with `<br/>`. And all content should faithfully exist in the code implementation.
- **Translations**: For translations, consult `AgentHeaven-docs/i18n.md` for the glossary.
- **SQLs**: Do **NOT** embed SQL strings directly in the code. Use `sqlalchemy` APIs instead (use ORM whenever possible). If necessary, use the resources folder.
- **Limited Exception Handling**: Avoid excessive logging and try-except blocks.

### Core Architecture Components
When working with AgentHeaven components:

1. **KLBase** (`ahvn/klbase/`) - Core component integrating storage and utilization layers
2. **KLStore** (`ahvn/klstore/`) - Storage layer supporting multiple backends (memory, file, database, remote)
3. **KLEngine** (`ahvn/klengine/`) - Utilization layer for knowledge retrieval and application
4. **LLM Module** (`ahvn/llm/`) - Uses LiteLLM for unified API across providers
5. **Tools** (`ahvn/tool/`) - FastMCP 2.0 based tool specifications
6. **UKF** (`ahvn/ukf/`) - Unified Knowledge Format implementation
7. **Cache** (`ahvn/cache/`) - Multi-level caching mechanisms
8. **Adapter** (`ahvn/adapter/`) - System adapters for different backends

## Development Commands & Scripts

**CRITICAL: Always use the provided scripts - never run pytest directly**

### Testing
```bash
# ALWAYS use the test script - NEVER use pytest directly
bash scripts/test.bash <args>

# Examples
bash scripts/test.bash                    # Run all tests
bash scripts/test.bash tests/test_file.py  # Run specific test
bash scripts/test.bash -k "test_name"     # Run tests matching pattern
```

### Code Formatting
```bash
# Format code with black and flake8
bash scripts/flake.bash -b -f

# -b: Run black first
# -f: Fix flake8 issues where possible
```

### Documentation
```bash
# Build and serve documentation (English and Chinese)
bash scripts/docs.bash en zh -s

# -s: Serve docs locally after building
```

### Git Commits
```bash
# For major features/refactors, include "[major]" to trigger full pytest on GitHub Actions
git commit -m "[major] Implement new knowledge storage backend"
```

## Release Process

This is the development repository. To create a public release:
```bash
bash release.bash  # Creates public repo and pushes to PyPI
```

**WARNING**: The public `AgentHeaven` repository should NEVER be touched directly.

## Key Features to Maintain

1. **Unified Knowledge Format (UKF)** - Standardized knowledge representation
2. **Imitator Architecture** - Lifelong learning via experience storage
3. **Multi-Backend Support** - Memory, file, database, remote storage
4. **FastMCP 2.0 Tools** - Extensible tool system
5. **LiteLLM Integration** - Unified API for all LLM providers
6. **Comprehensive Caching** - Multi-level caching for performance
7. **Bilingual Documentation** - English and Chinese support

## Repository Boundaries

- **Only** make changes in the `-dev` folders for AgentHeaven
- **NEVER** touch the public `AgentHeaven` repository
- **ALL** resources go in `src/ahvn/resources/`
- **NO** embedded SQL strings - use SQLAlchemy APIs