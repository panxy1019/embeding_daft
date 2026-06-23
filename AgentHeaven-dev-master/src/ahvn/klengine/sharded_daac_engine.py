__all__ = [
    "ShardedDAACKLEngine",
]

from ahvn.utils.basic.file_utils import touch_dir, list_dirs, delete_dir
from ahvn.utils.basic.str_utils import is_spacy_available, normalize_text, indexed_normalize_text, resolve_match_conflicts
from ahvn.utils.basic.config_utils import CM_AHVN
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.progress_utils import Progress, NoProgress

from ahvn.ukf.base import BaseUKF
from ahvn.klstore.base import BaseKLStore
from ahvn.klengine.base import BaseKLEngine
from ahvn.klengine.daac_engine import DAACKLEngine

import heapq
import os
import sys
from typing import Any, Dict, List, Optional, Iterable, Literal, Callable, Union, Type
from collections import defaultdict

logger = get_logger(__name__)


class ShardedDAACKLEngine(BaseKLEngine):
    """\
    A wrapper module that manages multiple daac_engine instances across data shards
    """

    inplace: bool = False
    recoverable: bool = False

    def __init__(
        self,
        storage: BaseKLStore,
        path: str,
        encoder: Optional[Callable[[BaseUKF], List[str]]] = None,
        min_length: int = 2,
        inverse: bool = True,
        normalizer: Optional[Union[Callable[[str], str], bool]] = None,
        name: Optional[str] = None,
        condition: Optional[Callable] = None,
        encoding: Optional[str] = "utf-8",
        max_shard_size: int = 10000,
        *args,
        **kwargs,
    ):
        super().__init__(storage=storage, name=name or f"{storage.name}_sharded_daac_idx", condition=condition, *args, **kwargs)

        self.path = path
        self.min_length = min_length
        self.inverse = inverse
        self.encoding = encoding or CM_AHVN.get("core.encoding", "utf-8")
        self.max_shard_size = max_shard_size

        if encoder is None:
            self.encoder = lambda kl: [kl.name] + (list(kl.synonyms) if kl.synonyms is not None else [])
        else:
            self.encoder = encoder

        if (normalizer is None) or isinstance(normalizer, bool):
            if normalizer and is_spacy_available():
                self.normalizer = normalize_text
                self.indexed_normalizer = indexed_normalize_text
            elif normalizer:
                logger.warning("spacy unavailable; falling back to simple lowercasing normalizer")
                self.normalizer = lambda text: text.lower()
                self.indexed_normalizer = None
            else:
                self.normalizer = lambda text: text
                self.indexed_normalizer = None
        else:
            self.normalizer = normalizer
            self.indexed_normalizer = None

        self.shards = []
        self.next_idx = 0

        touch_dir(self.path)
        if not self.load():
            self.save()

    def _init_shards(self):
        self.shards = []
        idx = -1
        for idx, shard_path in enumerate(list_dirs(self.path, abs=False)):
            # Assume the shards are int the directories 000, 001, ...
            if f"{idx:03d}" != shard_path:
                raise ValueError(f"Unexpected idx {idx} shard_path ({self.path}/{shard_path})")
            self.shards.append(self._alloc_new_shard(idx))
        self.next_idx = idx + 1
        heapq.heapify(self.shards)

    def _alloc_new_shard(self, idx: int):
        if idx > 999:
            raise ValueError(f"Too Many ({idx + 1}) shards")

        idx_str = f"{idx:03d}"
        # Pass None as storage as we do not have sharded storages
        shard = DAACKLEngine(
            storage=None,
            path=os.path.join(self.path, idx_str),
            encoder=self.encoder,
            min_length=self.min_length,
            inverse=self.inverse,
            normalizer=self.normalizer,
            name=f"{self.name}_{idx_str}",
            condition=self.condition,
            encoding=self.encoding,
        )
        return shard

    def __len__(self):
        return sum(len(shard) for shard in self.shards)

    def _has(self, key: int) -> bool:
        for shard in self.shards:
            if shard._has(key):
                return True
        return False

    def _search(
        self,
        query: str = "",
        conflict: Literal["overlap", "longest", "longest_distinct"] = "overlap",
        whole_word: bool = False,
        include: Optional[Iterable[str]] = None,
        *args,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        include_set = set(include) if include is not None else {"id", "kl", "strs"}
        if self.indexed_normalizer is not None:
            normalized_query, char_origins = self.indexed_normalizer(query)
        else:
            normalized_query = self.normalizer(query)
            char_origins = None

        results = []
        for shard in self.shards:
            shard_results = shard._search(query=query, conflict="overlap", whole_word=whole_word, include={"id", "matches"}, normalized_query=normalized_query)
            results.extend(shard_results)

        # Apply conflict resolution using utility function
        results = resolve_match_conflicts(
            results=results,
            conflict=conflict,
            query_length=len(normalized_query),
            inverse=self.inverse,
        )
        if char_origins is not None:
            strs_source = query
            results = [{**result, "matches": [(char_origins[s][0], char_origins[e - 1][1]) for s, e in result["matches"]]} for result in results]
        else:
            strs_source = normalized_query

        results = [
            {
                **({"id": result["id"]} if "id" in include_set else {}),
                **({"matches": result["matches"]} if "matches" in include_set else {}),
                **({"strs": [strs_source[start:end] for start, end in result["matches"]]} if "strs" in include_set else {}),
                **({"query": normalized_query} if "query" in include_set else {}),
            }
            for result in results
        ]

        return results

    def _get_synonyms_approximate_size(self, kls: Iterable[BaseUKF]):
        total = 0
        for kl in kls:
            if kl.normalized_synonyms:
                total += len(kl.normalized_synonyms)
            else:
                total += len(self.encoder(kl))
        return total

    def _upsert(self, kl: BaseUKF, flush: bool = True, **kwargs):
        if not self.shards or (self.shards[0].synonyms_size() + self._get_synonyms_approximate_size([kl]) > self.max_shard_size):
            heapq.heappush(self.shards, self._alloc_new_shard(self.next_idx))
            self.next_idx += 1
        self.shards[0]._upsert(kl, flush=flush, **kwargs)
        heapq.heapify(self.shards)

    def _batch_upsert(self, kls: Iterable[BaseUKF], flush: bool = True, progress: Progress = None, **kwargs):
        # We assume that a shard is sufficient to accommodate the synonyms
        # contained in one batch of KLs
        if not self.shards or (self.shards[0].synonyms_size() + self._get_synonyms_approximate_size(kls) > self.max_shard_size):
            heapq.heappush(self.shards, self._alloc_new_shard(self.next_idx))
            self.next_idx += 1
        self.shards[0]._batch_upsert(kls, flush=flush, progress=progress, **kwargs)
        heapq.heapify(self.shards)

    def _remove(self, key: int, flush: bool = True, **kwargs) -> bool:
        for shard in self.shards:
            if key in shard:
                shard._remove(key, flush=flush, **kwargs)
        heapq.heapify(self.shards)

    def _batch_remove(self, keys: Iterable[int], flush: bool = True, progress: Progress = None, **kwargs):
        # 1. When the number of shards is large, the time cost of sequential
        #    lookup is negligible compared to the time of rebuild
        # 2. To improve lookup performance, a lookup table can be maintained;
        #    however, when the number of kls is large, the memory consumption
        #    of the lookup table becomes non-negligible
        shard_idx_to_keys = defaultdict(list)
        for key in keys:
            for idx, shard in enumerate(self.shards):
                if key in shard:
                    shard_idx_to_keys[idx].append(key)
                    # A key resides in only one shard
                    break
        for idx, keys in shard_idx_to_keys.items():
            self.shards[idx]._batch_remove(keys, flush=flush, progress=progress, **kwargs)
        heapq.heapify(self.shards)

    def flush(self):
        for shard in self.shards:
            shard.flush()

    def sync(self, batch_size: Optional[int] = None, flush: bool = True, progress: Type[Progress] = None, **kwargs):
        self.clear()  # Remove all existing KLs for synchronization
        batch_size = batch_size or CM_AHVN.get("klengine.sync_batch_size", 512)
        num_kls = len(self.storage)
        total = num_kls
        batch_iter = self.storage.batch_iter(batch_size=batch_size)
        progress_cls = progress or NoProgress
        with progress_cls(total=total, desc=f"Syncing KLEngine '{self.name}'") as pbar:
            for kl_batch in batch_iter:
                self.batch_upsert(kl_batch, flush=False, progress=None, **kwargs)
                pbar.update(len(kl_batch))
        if flush:
            self.flush()

    def save(self, path: str = None):
        for shard in self.shards:
            shard.save()

    def load(self, path: str = None) -> bool:
        if not self.shards:
            self._init_shards()
        return any(shard.load() for shard in self.shards)

    def __sizeof__(self) -> int:
        return sum(shard.__sizeof__() for shard in self.shards) + sys.getsizeof(self.shards)

    def getsizeof(self) -> int:
        return self.__sizeof__()

    def _clear(self):
        for shard in self.shards:
            shard.clear()
        for shard_path in list_dirs(self.path, abs=True, reverse=True):
            delete_dir(shard_path)
        self.shards = []
        self.next_idx = 0

    def clear(self, **kwargs):
        self._clear()

    def close(self):
        for shard in self.shards:
            shard.close()
        self.shards = []
        self.next_idx = 0
