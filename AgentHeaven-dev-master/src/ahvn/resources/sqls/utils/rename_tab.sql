sqlite:     ALTER TABLE {old_name} RENAME TO {new_name};
postgresql: ALTER TABLE {old_name} RENAME TO {new_name};
mysql:      RENAME TABLE {old_name} TO {new_name};
duckdb:     ALTER TABLE {old_name} RENAME TO {new_name};
mssql:      EXEC sp_rename '{old_name}', '{new_name}', 'OBJECT';
oracle:     RENAME {old_name} TO {new_name};
starrocks:  ALTER TABLE {old_name} RENAME {new_name};
hive:       ALTER TABLE {old_name} RENAME TO {new_name};
trino:      ALTER TABLE {old_name} RENAME TO {new_name};
