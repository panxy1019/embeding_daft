"""Tests for AhvnConfigManager setup orchestration hooks."""

import ahvn.utils.basic.config_utils as cu


def test_setup_reset_clears_toolkit_capsule_and_translation(monkeypatch):
    monkeypatch.setattr(cu.ConfigManager, "setup", lambda self, reset=False: True)

    touched = []
    monkeypatch.setattr(cu, "_touch_dir", lambda path, clear=False: touched.append((path, clear)))

    class FakeStore:
        """Fake store that tracks init and clear calls."""

        def __init__(self, name):
            self.name = name
            self.cleared = False

        def clear(self):
            self.cleared = True
            return 0

    fake_toolkit = FakeStore("toolkit")
    fake_capsule = FakeStore("capsule")
    fake_prompt = FakeStore("prompt")
    fake_translation = FakeStore("translation")

    import ahvn.tool.store as tool_store_mod
    import ahvn.utils.capsule.store as capsule_store_mod
    import ahvn.utils.prompt.prompt_spec as prompt_spec_mod
    import ahvn.utils.prompt.prompt_store as prompt_store_mod
    import ahvn.utils.prompt.translate as translate_mod

    monkeypatch.setattr(tool_store_mod, "get_toolkit_store", lambda: fake_toolkit)
    monkeypatch.setattr(capsule_store_mod, "get_capsule_store", lambda: fake_capsule)
    pm_calls = []
    system_prompt_calls = []
    monkeypatch.setattr(prompt_spec_mod, "get_prompt_manager", lambda: pm_calls.append(True) or object())
    monkeypatch.setattr(prompt_spec_mod, "setup_system_prompts", lambda force=False: system_prompt_calls.append(force) or {})
    monkeypatch.setattr(prompt_store_mod, "get_prompt_store", lambda: fake_prompt)
    monkeypatch.setattr(translate_mod, "get_translation_store", lambda: fake_translation)

    cm = object.__new__(cu.AhvnConfigManager)
    cm._singleton_sig = ("ahvn", "agent-heaven", "ahvn")
    cm.get = lambda key, default=None: "%/tmp-test" if key == "core.tmp_path" else "%/cache-test" if key == "core.cache_path" else default

    ok = cu.AhvnConfigManager.setup(cm, reset=True)
    assert ok is True
    assert len(touched) == 2
    assert touched[0][1] is True
    assert touched[1][1] is True
    assert touched[0][0].endswith("tmp-test")
    assert touched[1][0].endswith("cache-test")
    assert fake_toolkit.cleared
    assert fake_capsule.cleared
    assert fake_prompt.cleared
    assert fake_translation.cleared
    assert len(pm_calls) == 1
    assert system_prompt_calls == [True]


def test_setup_non_reset_initializes_stores_without_clearing(monkeypatch):
    monkeypatch.setattr(cu.ConfigManager, "setup", lambda self, reset=False: False)

    touched = []
    monkeypatch.setattr(cu, "_touch_dir", lambda path, clear=False: touched.append((path, clear)))

    class FakeStore:
        def __init__(self, name):
            self.name = name
            self.cleared = False

        def clear(self):
            self.cleared = True
            return 0

    fake_toolkit = FakeStore("toolkit")
    fake_capsule = FakeStore("capsule")
    fake_prompt = FakeStore("prompt")
    fake_translation = FakeStore("translation")

    import ahvn.tool.store as tool_store_mod
    import ahvn.utils.capsule.store as capsule_store_mod
    import ahvn.utils.prompt.prompt_spec as prompt_spec_mod
    import ahvn.utils.prompt.prompt_store as prompt_store_mod
    import ahvn.utils.prompt.translate as translate_mod

    monkeypatch.setattr(tool_store_mod, "get_toolkit_store", lambda: fake_toolkit)
    monkeypatch.setattr(capsule_store_mod, "get_capsule_store", lambda: fake_capsule)
    pm_calls = []
    system_prompt_calls = []
    monkeypatch.setattr(prompt_spec_mod, "get_prompt_manager", lambda: pm_calls.append(True) or object())
    monkeypatch.setattr(prompt_spec_mod, "setup_system_prompts", lambda force=False: system_prompt_calls.append(force) or {})
    monkeypatch.setattr(prompt_store_mod, "get_prompt_store", lambda: fake_prompt)
    monkeypatch.setattr(translate_mod, "get_translation_store", lambda: fake_translation)

    cm = object.__new__(cu.AhvnConfigManager)
    cm._singleton_sig = ("ahvn", "agent-heaven", "ahvn")
    cm.get = lambda key, default=None: default

    ok = cu.AhvnConfigManager.setup(cm, reset=False)
    assert ok is False
    assert touched == []
    assert not fake_toolkit.cleared
    assert not fake_capsule.cleared
    assert not fake_prompt.cleared
    assert not fake_translation.cleared
    assert len(pm_calls) == 1
    assert system_prompt_calls == [False]


def test_setup_bootstrap_path_skips_store_imports(monkeypatch):
    monkeypatch.setattr(cu.ConfigManager, "setup", lambda self, reset=False: True)
    import ahvn.utils.prompt.prompt_spec as prompt_spec_mod

    pm_calls = []
    system_prompt_calls = []
    monkeypatch.setattr(prompt_spec_mod, "get_prompt_manager", lambda: pm_calls.append(True) or object())
    monkeypatch.setattr(prompt_spec_mod, "setup_system_prompts", lambda force=False: system_prompt_calls.append(force) or {})

    cm = object.__new__(cu.AhvnConfigManager)
    cm.get = lambda key, default=None: default

    ok = cu.AhvnConfigManager.setup(cm, reset=False)
    assert ok is True
    assert pm_calls == []
    assert system_prompt_calls == []
