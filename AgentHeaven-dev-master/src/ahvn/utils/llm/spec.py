__all__ = [
    "LLMSpec",
    "LLMConfigEngine",
    "LLM_CONFIG_ENGINE",
]

from ..basic.log_utils import get_logger

logger = get_logger(__name__)
from ..basic.misc_utils import unique
from ..basic.config_utils import CM_AHVN
from ..basic.config_spec import ConfigSpec, ConfigEngine
from ..basic.debug_utils import raise_mismatch

from pydantic import ConfigDict, Field
from typing import Dict, Any, Optional, Union, Literal
from copy import deepcopy


class LLMSpec(ConfigSpec):
    model: str
    provider: str
    backend: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        validate_assignment=True,
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preset": self.preset,
            "model": self.model,
            "provider": self.provider,
            "backend": self.backend,
            **self.args,
        }


def _normalize_alias(identifier: str, normalize: bool = True) -> str:
    """Normalize model identifier for mismatch checking."""
    if not normalize:
        return identifier
    return identifier.replace("-", "").replace("_", "").replace(".", "").replace(":", "").replace(" ", "").lower()


def _resolve_llm_aliases(model_alias: Optional[str] = None, normalize: bool = False) -> Union[Dict[str, Dict[str, str]], Dict[str, str]]:
    """
    Build mapping from alias -> { "": canonical_model, "<provider>": provider_identifier, ... }
    If model_alias is provided, return mapping for that alias (or {'': model_alias}).
    """
    models_cfg = CM_AHVN.get("llm", dict()).get("models", dict())
    mapping = dict(
        sorted(
            {
                _normalize_alias(alias, normalize): {**mc.get("identifiers", dict()), "": canonical}
                for canonical, mc in models_cfg.items()
                for alias in unique([canonical] + mc.get("aliases", list()) + list(mc.get("identifiers", dict()).values()))
                if alias is not None
            }.items()
        )
    )
    if model_alias is None:
        return mapping
    return mapping.get(_normalize_alias(model_alias, normalize), {"": model_alias})


class LLMConfigEngine(ConfigEngine[LLMSpec]):
    """\
    Configuration engine for LLM resources.

    Important behaviors:
    - resolve: deterministic; builds canonical LLMSpec. Does NOT convert backend into model.
                It also accepts materialized dicts (the output of materialize) and inverts them.
    - validate: asserts minimal requirements for materialize to succeed.
    - materialize: builds flat dict for LiteLLM (prefixes backend to provider identifier).
                Optionally resolves environment placeholders.
    """

    def resolve(self, config: Dict[str, Any] | LLMSpec, override: Optional[Dict[str, Any]] = None) -> LLMSpec:
        """\
        Resolve a configuration dictionary (or existing ``LLMSpec``) into a
        canonical ``LLMSpec``.

        Priority (low → high): global default_args → model default_args →
        provider args → preset default_args → user kwargs.

        If *config* is already an ``LLMSpec``, it is returned as-is (idempotent).

        Args:
            config: User configuration dict or existing spec.
            override: If provided, used as the ``llm`` configuration block
                instead of reading from ``CM_AHVN.get("llm", ...)``.  This
                breaks the ``CM_AHVN`` dependency for boot-time or testing
                contexts.
        """
        # Idempotent: already resolved
        if isinstance(config, LLMSpec):
            return config

        cfg = dict(config)

        if override is not None:
            llms_cfg = override
        else:
            llms_cfg = CM_AHVN.get("llm", dict())
        presets_cfg = llms_cfg.get("presets", dict())
        providers_cfg = llms_cfg.get("providers", dict())
        models_cfg = llms_cfg.get("models", dict())
        default_preset = llms_cfg.get("default_preset", "sys")
        default_provider = llms_cfg.get("default_provider", None)
        default_args = llms_cfg.get("default_args", dict())
        providers_default_args = providers_cfg.get("default_args", dict())
        model_mismatch = llms_cfg.get("model_mismatch", "ignore")
        model_mismatch_suggestion_thres = llms_cfg.get("model_mismatch_suggestion_thres", 0.3)
        model_mismatch_suggestion_case = llms_cfg.get("model_mismatch_suggestion_case", False)
        model_normalize = llms_cfg.get("model_normalize", True)

        # --- Resolve preset ---
        preset = cfg.get("preset", None) or default_preset
        if preset and (preset not in presets_cfg):
            raise_mismatch(presets_cfg, got=preset, name="preset", mode="raise")
        preset_cfg = presets_cfg.get(preset, dict())
        preset_model = preset_cfg.get("model", None)
        if not preset_model:
            raise ValueError(f"Preset '{preset}' must specify a non-empty 'model'. Got: {preset_model}.")
        preset_provider = preset_cfg.get("provider", None)
        preset_default_args = preset_cfg.get("default_args", dict())

        # --- Resolve model ---
        model = cfg.get("model", None)
        if not model:
            model = preset_model
        if not model:
            raise ValueError(f"Model identifier must be non-empty. Got: {model}.")
        canonical_model = _resolve_llm_aliases(model_alias=model, normalize=model_normalize).get("", None)
        if not canonical_model:
            raise ValueError(f"Canonical model identifier in the config must be non-empty. Got: {model}.")
        if canonical_model not in models_cfg:
            choice = raise_mismatch(
                models_cfg,
                got=model,
                name="model",
                mode=model_mismatch,
                thres=model_mismatch_suggestion_thres,
                case_sensitive=model_mismatch_suggestion_case,
                normalizer=lambda x: _normalize_alias(x, normalize=model_normalize),
            )
            if (model_mismatch in ["match", "warn"]) and choice:
                canonical_model = choice
        model_cfg = models_cfg.get(canonical_model, dict())
        model_default_args = model_cfg.get("default_args", dict())

        # --- Resolve provider ---
        provider = cfg.get("provider", None)
        if not provider:
            provider = preset_provider
        if not provider:
            provider = model_cfg.get("default_provider", None)
        if not provider:
            model_providers = list(model_cfg.get("identifiers", dict()))
            provider = model_providers[0] if model_providers else default_provider
        if not provider:
            raise ValueError(f"Provider must be non-empty. Got: {provider}.")
        if provider not in providers_cfg:
            raise_mismatch(providers_cfg, got=provider, name="provider", mode="raise")

        provider_args = providers_cfg.get(provider, dict())
        provider_backend = provider_args.get("backend", "")
        provider_model_id = model_cfg.get("identifiers", dict()).get(provider, canonical_model)
        provider_model_args = provider_args.get("model_args", dict()).get(provider_model_id, dict())
        provider_args = {k: v for k, v in provider_args.items() if k not in ["backend", "model_args", "default_args"]}

        args = dict()
        args.update(deepcopy(default_args))
        args.update(deepcopy(providers_default_args))
        args.update(deepcopy(provider_args))
        args.update(deepcopy(model_default_args))
        args.update(deepcopy(provider_model_args))
        args.update(deepcopy(preset_default_args))
        args.update(deepcopy(cfg))

        # Finalize: preset, provider, model, backend, args
        backend = args.get("backend")
        if backend is None:
            backend = provider_backend
        args.pop("preset", None)
        args.pop("provider", None)
        args.pop("model", None)
        args.pop("backend", None)

        args = self.standardize(args)

        return LLMSpec(
            preset=preset,
            model=provider_model_id,
            provider=provider,
            backend=backend,
            args=args,
        )

    def validate(self, config: LLMSpec) -> bool:
        """\
        Check that the resolved spec has the minimum information needed
        to make a LiteLLM call.
        """
        if not config.model:
            raise ValueError("LLMSpec.model is required")
        if not config.provider:
            raise ValueError("LLMSpec.provider is required")
        if config.backend is None:
            raise ValueError("LLMSpec.backend cannot be None (use empty string for no backend)")
        return True

    def materialize(self, config: LLMSpec, mode: Literal["default", "spec", "litellm"] = "default") -> Dict[str, Any]:
        """\
        Extract the relevant information from the resolved spec to produce a flat configuration dictionary.

        Args:
            config (LLMSpec): The resolved LLM specification to materialize.
            mode (Literal["default", "spec", "litellm"]): The materialization mode. Defaults to "default".
                - "default": Alias for "litellm".
                - "spec": Returns the full configuration as a dictionary (including preset, provider, backend, and args).
                - "litellm": Returns the LiteLLM-ready configuration dictionary (with backend prefixed to model).
        """
        if mode == "spec":
            return config.to_dict()
        if config.backend:
            litellm_model = f"{config.backend}/{config.model}"
        else:
            litellm_model = config.model
        return deepcopy({**config.args, "model": litellm_model})


LLM_CONFIG_ENGINE = LLMConfigEngine()
