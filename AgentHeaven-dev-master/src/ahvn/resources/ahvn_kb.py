__all__ = [
    "HEAVEN_KB",
    "setup_heaven_kb",
]

from ahvn.cache import JsonCache
from ahvn.klstore.cache_store import CacheKLStore
from ahvn.klengine.scan_engine import ScanKLEngine
from ahvn.klbase.base import KLBase
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.utils.prompt import PM_AHVN, PromptSpec, setup_system_prompts

from ahvn.utils.basic.config_utils import CM_AHVN
from ahvn.utils.basic.log_utils import get_logger

logger = get_logger(__name__)


class AhvnKLBase(KLBase):
    def __init__(self):
        super().__init__(name="ahvn")
        self.add_storage(
            CacheKLStore(
                name="_prompts",
                cache=JsonCache(CM_AHVN.pj("&/ukfs/prompts")),
            )
        )
        self.add_engine(
            ScanKLEngine(
                name="prompts",
                storage=self.storages["_prompts"],
            )
        )

    def get_prompt(self, name: str, **kwargs) -> PromptUKFT:
        results = self.search(engine="prompts", name=name, **kwargs)
        if not results:
            raise ValueError(f"Prompt '{name}' not found in HEAVEN_KB.")
        if len(results) > 1:
            raise ValueError(f"Multiple prompts named '{name}' found in HEAVEN_KB. Please refine your search facets by adding `**kwargs`.")
        return results[0]["kl"]


HEAVEN_KB = AhvnKLBase()


def setup_heaven_kb():
    logger.info("Re-generating HEAVEN_KB...")
    HEAVEN_KB.clear()
    setup_system_prompts(force=False)

    mappings = [
        ("default_prompt", "prompt"),
        ("translation_prompt", "translation"),
        ("toolspec_prompt", "toolspec"),
        ("experience_prompt", "experience"),
        ("autotask_prompt", "autotask"),
        ("autotask_prompt_base", "autotask_base"),
        ("autotask_prompt_repr", "autotask_repr"),
        ("autotask_prompt_json", "autotask_json"),
        ("autotask_prompt_code", "autotask_code"),
        ("autocode_prompt", "autocode"),
        ("autofunc_prompt", "autofunc"),
    ]
    prompts = []
    for prompt_id, name in mappings:
        spec = PM_AHVN.get(prompt_id, version=0)
        if not isinstance(spec, PromptSpec):
            raise ValueError(f"Prompt '{prompt_id}' not found in PM_AHVN.")
        prompts.append(PromptUKFT.from_spec(spec, name=name))
    HEAVEN_KB.batch_upsert(
        prompts,
        storages=["_prompts"],
    )


# Temporary trigger for initial setup
if (len(HEAVEN_KB.storages["_prompts"]) == 0) or (CM_AHVN.get("core.debug")):
    setup_heaven_kb()


if __name__ == "__main__":
    setup_heaven_kb()

    # Debug
    for r in HEAVEN_KB.search(engine="prompts", name="autocode"):
        print(r["kl"].name)
    exit(0)
