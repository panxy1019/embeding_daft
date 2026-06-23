"""Tests for built-in system prompt registration in PM_AHVN."""

from ahvn.utils.prompt import PromptSpec, get_system_prompt_spec, setup_system_prompts
from ahvn.utils.prompt.prompt_spec import _PROMPT_REGISTRY
from ahvn.utils.prompt.prompt_store import PromptStore
from ahvn.utils.prompt.translate import TranslationStore


def _patch_prompt_backends(monkeypatch, tmp_path):
    prompt_store = PromptStore(provider="sqlite", database=f"file:{tmp_path / 'prompt_registry.db'}")
    tr_store = TranslationStore(provider="sqlite", database=str(tmp_path / "prompt_registry_tr.db"))
    monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_prompt_store", lambda: prompt_store)
    monkeypatch.setattr("ahvn.utils.prompt.prompt_spec.get_translation_store", lambda: tr_store)
    return prompt_store, tr_store


def test_setup_system_prompts_registers_version_zero(monkeypatch, tmp_path):
    _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()

    setup_system_prompts(force=True)
    spec = get_system_prompt_spec("default_prompt", version=0)

    assert spec.id == "default_prompt"
    assert spec.version == 0
    assert spec.prompt_type == "func"


def test_setup_system_prompts_persists_version_zero_after_reload(monkeypatch, tmp_path):
    _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()

    setup_system_prompts(force=True)
    _PROMPT_REGISTRY.clear()

    spec = get_system_prompt_spec("default_prompt", version=0)
    assert spec.version == 0


def test_get_system_prompt_spec_uses_latest_override(monkeypatch, tmp_path):
    _patch_prompt_backends(monkeypatch, tmp_path)
    _PROMPT_REGISTRY.clear()

    setup_system_prompts(force=True)
    PromptSpec.from_str("Overridden {name}", id="default_prompt", version=2)

    latest = get_system_prompt_spec("default_prompt")
    assert latest.version == 2
    assert latest(name="Alice") == "Overridden Alice"

    fallback_v0 = get_system_prompt_spec("default_prompt", version=0)
    assert fallback_v0.version == 0
