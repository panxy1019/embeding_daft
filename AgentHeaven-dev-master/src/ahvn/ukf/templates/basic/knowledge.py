__all__ = [
    "KnowledgeUKFT",
]

from ahvn.klbase import KLBase
from ...base import BaseUKF
from ...registry import register_ukft

from typing import ClassVar, Optional, Set, Dict, Any


@register_ukft
class KnowledgeUKFT(BaseUKF):
    """\
    General-purpose knowledge entity for storing diverse information types.

    UKF Type: knowledge
    Recommended Components of `content_resources`:
        None

    Recommended Composers:
        Any
    """

    type_default: ClassVar[str] = "knowledge"

    @classmethod
    def from_desc(
        cls,
        content: str,
        klbase: Optional[KLBase] = None,
        name: Optional[str] = None,
        short_description: Optional[str] = None,
        description: Optional[str] = None,
        synonyms: Optional[Set[str]] = None,
        content_resources: Optional[Dict[str, Any]] = None,
        rel: str = "mentioned",
        mentiond_ids: Optional[Set[str]] = None,
        **kwargs,
    ) -> "KnowledgeUKFT":
        """Create KnowledgeUKFT from content and link @mentions."""
        knowledge = cls(
            name=name or content,
            short_description=short_description or "",
            description=description or content,
            synonyms=synonyms or set(),
            content=content,
            content_resources=content_resources or dict(),
            **kwargs,
        )

        if klbase is None:
            return knowledge
        storage = klbase.storages.get("main")
        if storage is None:
            return knowledge

        for mentiond_id in mentiond_ids:
            d_kl = storage.get(mentiond_id, default=None)
            if d_kl is None:
                continue
            knowledge.link(dir="object", rel=rel, kl=d_kl)

        return knowledge
