import sqlalchemy as sa

from ahvn.utils.db.spec import DATABASE_CONFIG_ENGINE


def _override_cfg() -> dict:
    return {
        "default_provider": "demo",
        "default_args": {},
        "providers": {
            "demo": {
                "dialect": "postgresql",
                "driver": "psycopg2",
                "host": "localhost",
                "port": 5432,
                "username": "app_user",
                "password": "app_pw",
                "database": "app_db",
                "params": {
                    "sslmode": "prefer",
                    "application_name": "ahvn",
                },
                "pool": {
                    "pool_class": "null",
                },
                "superuser": {
                    "database": "postgres",
                    "params": {
                        "connect_timeout": "5",
                    },
                },
            }
        },
    }


def test_superuser_materialize_falls_back_database_when_missing():
    override = _override_cfg()
    override["providers"]["demo"]["superuser"] = {
        "host": "su-host",
    }
    spec = DATABASE_CONFIG_ENGINE.resolve({"provider": "demo"}, override=override)

    su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
    su_url = sa.engine.make_url(su_kw["url"])

    assert su_url.host == "su-host"
    assert su_url.database == "app_db"


def test_superuser_materialize_merges_params_with_user_overrides():
    spec = DATABASE_CONFIG_ENGINE.resolve({"provider": "demo"}, override=_override_cfg())

    su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
    su_url = sa.engine.make_url(su_kw["url"])
    q = dict(su_url.query)

    assert q["sslmode"] == "prefer"
    assert q["application_name"] == "ahvn"
    assert q["connect_timeout"] == "5"


def test_superuser_materialize_superuser_params_override_regular_params():
    override = _override_cfg()
    override["providers"]["demo"]["superuser"]["params"]["sslmode"] = "require"
    spec = DATABASE_CONFIG_ENGINE.resolve({"provider": "demo"}, override=override)

    su_kw = DATABASE_CONFIG_ENGINE.materialize(spec, mode="superuser")
    su_url = sa.engine.make_url(su_kw["url"])
    q = dict(su_url.query)

    assert q["sslmode"] == "require"


def test_oracle_service_name_keeps_database_out_of_url_path():
    override = {
        "default_provider": "oracle-demo",
        "default_args": {},
        "providers": {
            "oracle-demo": {
                "dialect": "oracle",
                "driver": "oracledb",
                "host": "localhost",
                "port": 1521,
                "username": "system",
                "password": "password",
                "database": "FREEPDB1",
                "params": {"service_name": "FREEPDB1"},
                "pool": {"pool_class": "null"},
            }
        },
    }
    spec = DATABASE_CONFIG_ENGINE.resolve({"provider": "oracle-demo"}, override=override)

    url = sa.engine.make_url(DATABASE_CONFIG_ENGINE.materialize(spec, mode="url"))

    assert url.database in (None, "")
    assert dict(url.query)["service_name"] == "FREEPDB1"
