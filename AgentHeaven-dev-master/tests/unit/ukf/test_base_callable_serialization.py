"""Tests for capsule-backed callable serialization in BaseUKF."""

import random

from ahvn.ukf.base import BaseUKF
from ahvn.ukf.templates.basic.skill import desc_composer as _base_desc_composer


def _trigger_even(_ukf, value: int = 0) -> bool:
    return value % 2 == 0


def _composer_echo(_ukf, text: str = "") -> str:
    return f"echo:{text}"


def _composer_module_global(_ukf, text: str = "") -> str:
    return f"{text}:{random.Random(0).randint(0, 0)}"


def _composer_reuse_template_skill_desc(ukf, **kwargs):
    return _base_desc_composer(ukf, **kwargs)


def test_triggers_are_serialized_as_capsules():
    ukf = BaseUKF(name="capsule-trigger", triggers={"even": _trigger_even})

    payload = ukf.to_dict()
    trigger_blob = payload["triggers"]["even"]
    assert isinstance(trigger_blob, dict)
    assert "capsule_version" in trigger_blob
    assert "layers" in trigger_blob


def test_content_composers_are_serialized_as_capsules():
    ukf = BaseUKF(name="capsule-composer", content_composers={"echo": _composer_echo})

    payload = ukf.to_dict()
    composer_blob = payload["content_composers"]["echo"]
    assert isinstance(composer_blob, dict)
    assert "capsule_version" in composer_blob
    assert "layers" in composer_blob


def test_capsule_callable_round_trip_for_triggers_and_composers():
    ukf = BaseUKF(
        name="capsule-round-trip",
        triggers={"even": _trigger_even},
        content_composers={"echo": _composer_echo},
    )

    restored = BaseUKF.from_dict(ukf.to_dict(), polymorphic=False)
    assert restored.triggers["even"](restored, value=4) is True
    assert restored.triggers["even"](restored, value=5) is False
    assert restored.content_composers["echo"](restored, text="ok") == "echo:ok"


def test_capsule_composer_round_trip_preserves_module_globals():
    ukf = BaseUKF(
        name="capsule-module-global",
        content_composers={"module_global": _composer_module_global},
    )

    restored = BaseUKF.from_dict(ukf.to_dict(), polymorphic=False)
    assert restored.content_composers["module_global"](restored, text="ok") == "ok:0"


def test_capsule_composer_requirements_include_reused_template_composer_module():
    ukf = BaseUKF(
        name="capsule-reuse-template-composer",
        content_composers={"desc_proxy": _composer_reuse_template_skill_desc},
    )

    payload = ukf.to_dict()
    capsule = payload["content_composers"]["desc_proxy"]
    source_layer = next(layer for layer in capsule["layers"] if layer["type"] == "source")
    requirements = source_layer.get("requirements", {})
    modules = requirements.get("modules", [])
    module_names = [item.get("name") for item in modules if isinstance(item, dict)]
    assert "ahvn.ukf.templates.basic.skill" in module_names


def test_capsule_composer_reuse_template_composer_restores_without_global_alias(monkeypatch):
    ukf = BaseUKF(
        name="capsule-reuse-template-composer-restore",
        description="restore test",
        content_composers={"desc_proxy": _composer_reuse_template_skill_desc},
    )
    payload = ukf.to_dict()
    monkeypatch.delitem(globals(), "_base_desc_composer", raising=False)

    restored = BaseUKF.from_dict(payload, polymorphic=False)
    text = restored.content_composers["desc_proxy"](restored)
    assert "<name>capsule-reuse-template-composer-restore</name>" in text
