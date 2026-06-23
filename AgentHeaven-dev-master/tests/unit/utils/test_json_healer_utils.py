import pytest

import ahvn
from ahvn.utils.basic.serialize_utils import heal_json
from ahvn.utils.basic import serialize_utils as healer_mod


def test_heal_json_valid_passthrough_string():
    payload = '{"a":1,"b":"x"}'
    assert heal_json(payload) == {"a": 1, "b": "x"}


def test_heal_json_valid_passthrough_object():
    payload = {"a": 1, "b": "x"}
    assert heal_json(payload) == {"a": 1, "b": "x"}


def test_heal_json_incomplete_json():
    payload = '{"a": 1'
    assert heal_json(payload) == {"a": 1}


def test_heal_json_key_grounding_case_and_normalized():
    payload = '{"User ID": 7, "USERName": "Ann", "extra": 9}'
    healed = heal_json(payload, schema=["user_id", "username"])
    assert healed["user_id"] == 7
    assert healed["username"] == "Ann"
    assert healed["extra"] == 9


def test_heal_json_key_grounding_drop_extras():
    payload = '{"User ID": 7, "USERName": "Ann", "extra": 9}'
    healed = heal_json(payload, schema=["user_id", "username"], drop_extras=True)
    assert healed == {"user_id": 7, "username": "Ann"}


def test_heal_json_sql_string_escaping_single_key():
    payload = '{"sql":"SELECT * FROM users WHERE name = "Alice""}'
    healed = heal_json(payload, schema={"sql": "string"})
    assert healed["sql"] == 'SELECT * FROM users WHERE name = "Alice"'


def test_heal_json_sql_string_escaping_multi_key():
    payload = '{"sql":"SELECT * FROM t WHERE name = "A"", "limit": 5}'
    healed = heal_json(payload, schema={"sql": "string", "limit": "integer"})
    assert healed["sql"] == 'SELECT * FROM t WHERE name = "A"'
    assert healed["limit"] == 5


def test_heal_json_optional_layers_missing(monkeypatch):
    real_import = healer_mod.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name in {"json_repair", "fix_busted_json"}:
            raise ImportError("missing optional healer")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(healer_mod.importlib, "import_module", fake_import)

    payload = '{"a": 1'
    assert heal_json(payload) == {"a": 1}


def test_heal_json_is_exported_for_easy_import():
    assert callable(ahvn.heal_json)


def test_heal_json_invalid_schema_type():
    with pytest.raises(TypeError):
        heal_json('{"a":1}', schema="invalid")
