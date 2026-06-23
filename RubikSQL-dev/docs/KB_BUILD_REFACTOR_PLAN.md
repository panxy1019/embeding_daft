# Build Stages

- [x] Build Column (create a ColumnUKFT for a single column from database statistics)
    -> CLI: `rubiksql build column [--name/-n] [--table/-t] [--column/-c] [--update/-u]`
        Notice that when specifying on database/table levels, all the columns should be built independently, parallely and with Rich Progress Bar.
        When not specifying update, skip building if ColumnUKFT already exists in the knowledge base (same for other build stages below).
- [ ] Build Column Anchor (create a ColumnUKFT with ANCHOR=True tag for a single column from user annotated `info.yaml`)
- [ ] Apply Column Anchors (update existing main ColumnUKFTs by merging anchor ColumnUKFTs)
- [x] Deduce Column Type (infer column datatype based onf the ColumnUKFT statistics, preferably after applying anchor knowledge)
    When user anchor knowledge specifies a column type, that type should be used directly.
    Otherwise, infer the type based on statistics (this may involve a small LLM call to determine types like datetime, etc.)
- [ ] Gen Column Description (optional: generate description from short_description to make it more detailed, using LLM)
    -> CLI: `rubiksql build column-desc [--name/-n] [--table/-t] [--column/-c] [--update/-u]`
    By default without this generation step, description = short_description = anchor `desc` in `info.yaml` (if null, reduce to a default string).
    Use LLM to expand short_description into a more detailed description.
- [ ] Gen Column Synonyms (optional: generate synonyms for the column using LLM)
    -> CLI: `rubiksql build column-synonyms [--name/-n] [--table/-t] [--column/-c] [--update/-u]`
    By default without this generation step, synonyms = [].
    Use LLM to generate a list of synonyms for the column name.

- [ ] Build Enums (create EnumUKFTs for all enum values in a column)
    -> CLI: `rubiksql build enums [--name/-n] [--table/-t] [--column/-c] [--update/-u]`
        Notice that when specifying on database/table/column levels, all the columns should be built independently, parallely and with Rich Progress Bar.
        Only some of the columns should build enums:
        1. If user specifies "enum_index_enabled: true" (stored in ColumnUKFT.get("enum_index")), always build enums for the column.
        2. If user specifies "enum_index_enabled: false" (stored in ColumnUKFT.get("enum_index")), never build enums for the column.
        3. If "enum_index_enabled: null" (stored in ColumnUKFT.get("enum_index")), build enums for TEXT and CATEGORICAL columns only (datatype stored in ColumnUKFT.get("datatype"), always use `ColumnType` class value instead of string).
- [ ] Apply Enum Anchors (update existing main EnumUKFTs by merging anchor EnumUKFTs)
- [ ] Gen Enum Description (optional: generate descriptions for enum values using LLM)
    -> CLI: `rubiksql build enum-desc [--name/-n] [--table/-t] [--column/-c] [--enum/-e] [--update/-u]`
    By default without this generation step, description = short_description = anchor `desc` in `info.yaml` (if null, reduce to a default string).
    Use LLM to expand short_description into a more detailed description.
- [ ] Gen Enum Synonyms (optional: generate synonyms for enum values using LLM)
    -> CLI: `rubiksql build enum-synonyms [--name/-n] [--table/-t] [--column/-c] [--enum/-e] [--update/-u]`
    By default without this generation step, synonyms = [].
    Use LLM to generate a list of synonyms for the enum value.


- [x] Build Table (create a TableUKFT for a single table from columns, preferably already merged with anchor knowledge)
    -> CLI: `rubiksql build table [--name/-n] [--table/-t] [--update/-u]`
        Notice that this requires all columns to be built first. The columns with `disabled: false` will be included.
        Currently the `TableUKFT.from_tab()` uses `Database.db_tabs()` to get all columns in the table, which is incorrect as it does not consider disabled columns.
- [ ] Apply Table Anchors (update existing main TableUKFTs by merging anchor TableUKFTs)
- [ ] Gen Table Description (optional: generate description from short_description to make it more detailed, using LLM)
    -> CLI: `rubiksql build table-desc [--name/-n] [--table/-t] [--update/-u]`
    By default without this generation step, description = short_description = anchor `desc` in `info.yaml` (if null, reduce to a default string).
    Use LLM to expand short_description into a more detailed description.
- [ ] Gen Table Synonyms (optional: generate synonyms for the table using LLM)
    -> CLI: `rubiksql build table-synonyms [--name/-n] [--table/-t] [--update/-u]`
    By default without this generation step, synonyms = [].
    Use LLM to generate a list of synonyms for the table name.

- [x] Build Database (create a DatabaseUKFT for the database from tables)
    -> CLI: `rubiksql build database [--name/-n] [--update/-u]`
        Notice that this requires all tables to be built first. The tables with `disabled: false` will be included.
- [ ] Apply Database Anchors (update existing main DatabaseUKFTs by merging anchor DatabaseUKFTs)
- [ ] Gen Database Description (optional: generate description from short_description to make it more detailed, using LLM)
    -> CLI: `rubiksql build database-desc [--name/-n] [--update/-u]`
    By default without this generation step, description = short_description = anchor `desc` in `info.yaml` (if null, reduce to a default string).
    Use LLM to expand short_description into a more detailed description.
- [ ] Gen Database Synonyms (optional: generate synonyms for the database using LLM)
    -> CLI: `rubiksql build database-synonyms [--name/-n] [--update/-u]`
    By default without this generation step, synonyms = [].
    Use LLM to generate a list of synonyms for the database name.

- [ ] Build NL2SQL Query (create an example NL-SQL pair as learning data RubikSQLExpUKFT)
- [ ] Build Mined Knowledge (update synonyms and create new PredicateUKFTs based on a given NL-SQL pair knowledge)
    - To Be Designed: How should the mined knowledge be stored? Especially the synonyms, do they work on the knowledge, or the anchor, or both?
- [ ] Build Curriculumn (create a series of augmented NL-SQL pairs based on a given NL-SQL pair knowledge)
- [ ] Build Template (create a RubikSQLTemplateUKFT from a given NL-SQL pair knowledge)
