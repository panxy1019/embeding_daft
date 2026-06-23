# Todo: Implement Enum Building and Enhance Type Display

## Background
We've completed basic build commands for column, table, and database. Now we need to:
1. Improve column type display in `rubiksql info` to show annotated types
2. Implement `rubiksql build enum` command for building enum knowledge
3. Add enum search support to `rubiksql search`

## Plan

### 1. Update `rubiksql info` to Display Annotated Column Types
- [x] **Task 1.1**: Modify table-level `info` output to show annotated datatype in addition to original
  - Current: `● period: VARCHAR`
  - Target: `● period: VARCHAR (INTEGER)` 
  - Show `[enum]` indicator if enum_index_enabled is True

### 2. Implement `rubiksql build enum` Command
- [x] **Task 2.1**: Add `build_enum()` function in `api/knowledge.py`
  - Support hierarchical scope: db_id, tab_id, col_id
  - Follow hierarchical pattern like `build_column()`
  - Use rich progress display for batch operations
  - Implement enum building rules:
    1. If `enum_index_enabled: true` → always build
    2. If `enum_index_enabled: false` → never build
    3. If `enum_index_enabled: null` → build for TEXT and CATEGORICAL columns only
  - Sign EnumUKFT with `system=True` before upserting
  - Skip existing unless `update=True`

- [x] **Task 2.2**: Add `build enum` CLI command in `cli/build_cli.py`
  - Follow same pattern as `build column` command
  - Options: `-n/--name`, `-t/--table`, `-c/--column`, `-u/--update`
  - Use RubikSQLCLIProgress for progress display
  - Validate hierarchy: column requires table

- [x] **Task 2.3**: Test enum building
  - Test on RubikBench database
  - Verify EnumUKFTs are created with proper signing
  - Verify rules for enum_index_enabled work correctly

### 3. Add Enum Search Support
- [x] **Task 3.1**: Update `search_knowledge()` in `api/knowledge.py`
  - Add `enum_val` parameter
  - When enum_val provided, require tab_id and col_id
  - Use `EnumUKFT.from_enum()` pattern to compose name for search

- [x] **Task 3.2**: Update `search` CLI command in `cli/search_cli.py`
  - Add `--enum/-e` option
  - Validate: -e requires both -t and -c
  - Update help text and examples

- [x] **Task 3.3**: Test enum search
  - Build enums for a column
  - Search for specific enum values
  - Verify search returns correct knowledge

### 4. Testing and Debugging
- [x] **Task 4.1**: Test complete workflow
  - `rubiksql info` shows proper type annotations
  - `rubiksql build enum` works at all hierarchy levels
  - `rubiksql search` supports enum search
  - All progress displays work correctly

## Review

### Changes Made

1. **Enhanced `rubiksql info` Display**
   - Modified [src/rubiksql/cli/rubiksql.py](src/rubiksql/cli/rubiksql.py) to show annotated column types in parentheses
   - Added `[enum]` indicator for columns with explicit enum_index_enabled
   - Example: `● period: VARCHAR (INTEGER) [enum]`

2. **Fixed Column Type Deduction to Update db_info**
   - Modified [src/rubiksql/api/knowledge.py](src/rubiksql/api/knowledge.py) `deduce_column_type()` function
   - Now saves deduced datatypes back to db_info.yaml after upserting to KB
   - Ensures annotated types persist and display correctly

3. **Implemented `build_enum()` API Function**
   - Added new `build_enum()` function in [src/rubiksql/api/knowledge.py](src/rubiksql/api/knowledge.py)
   - Supports hierarchical building (database/table/column levels)
   - Implements enum eligibility rules:
     - Explicit enable: `enum_index_enabled = True` → always build
     - Explicit disable: `enum_index_enabled = False` → never build
     - Default: `enum_index_enabled = None` → build for TEXT and CATEGORICAL only
   - Uses parallel processing with progress tracking
   - Signs all EnumUKFTs with `system=True` before upserting
   - Exported in [src/rubiksql/api/__init__.py](src/rubiksql/api/__init__.py)

4. **Implemented `rubiksql build enum` CLI Command**
   - Added `build enum` command in [src/rubiksql/cli/build_cli.py](src/rubiksql/cli/build_cli.py)
   - Follows same pattern as other build commands
   - Options: `-n/--name`, `-t/--table`, `-c/--column`, `-u/--update`
   - Updated help text in build group to include enum subcommand

5. **Enhanced `search_knowledge()` for Enum Search**
   - Modified [src/rubiksql/api/knowledge.py](src/rubiksql/api/knowledge.py) to add `enum_val` parameter
   - Composes enum name using `value_repr()` like `EnumUKFT.from_enum()` does
   - Searches by full name: `'TABLE'.'COLUMN'='VALUE'`
   - Validates hierarchy: enum_val requires both tab_id and col_id

6. **Enhanced `rubiksql search` CLI for Enum Search**
   - Added `--enum/-e` option in [src/rubiksql/cli/search_cli.py](src/rubiksql/cli/search_cli.py)
   - Validates: -e requires both -t and -c
   - Updated help text with enum search examples
   - Improved error messages to include enum in entity description

### Testing Results

All features tested successfully on RubikBench database:

1. **Type Display**: 
   - Column types show correctly: `VARCHAR (CATEGORICAL)`, `VARCHAR (INTEGER)`
   - Enum indicator shows for explicitly enabled columns: `[enum]`

2. **Enum Building**:
   - Built 780 enums for 39 CATEGORICAL columns in INCOME_ACCESSORY table
   - Built 72 enums for period column with explicit enum_index_enabled
   - Correctly skipped INTEGER columns without explicit enum_index
   - Progress bar and batch processing work correctly

3. **Enum Search**:
   - Successfully searched for enum `Accessories` in `prod_lv0_name_en`
   - Successfully searched for enum `202111` in `period`
   - Returns proper EnumUKFT with tags, synonyms, and content

### Key Implementation Details

- Used `kb.search(engine="facet", type="db-enum", tags={...})` for finding existing enums
- Used `Database.col_freqs()` to get all distinct values for enum building
- Properly signed all EnumUKFTs with `.signed(system=True, verified=True)`
- Followed hierarchical scope pattern consistently across API and CLI
- Used RubikSQLRichProgress for all batch operations

### Files Modified

- [src/rubiksql/cli/rubiksql.py](src/rubiksql/cli/rubiksql.py) - Enhanced info display
- [src/rubiksql/api/knowledge.py](src/rubiksql/api/knowledge.py) - Added build_enum, enhanced search_knowledge, fixed deduce_column_type
- [src/rubiksql/api/__init__.py](src/rubiksql/api/__init__.py) - Exported build_enum
- [src/rubiksql/cli/build_cli.py](src/rubiksql/cli/build_cli.py) - Added build enum command
- [src/rubiksql/cli/search_cli.py](src/rubiksql/cli/search_cli.py) - Added enum search support

## Notes
- Use RubikBench database (already activated) for testing
- Follow existing patterns in build_column and search_knowledge
- Ensure all EnumUKFTs are signed before upserting
- Use ColumnType enum values, not strings, for datatype comparisons
- Use `repr()` to compose enum names like EnumUKFT.from_enum does
