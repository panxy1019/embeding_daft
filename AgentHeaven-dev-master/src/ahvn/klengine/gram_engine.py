__all__ = [
    "GRAMKLEngine",
]

from ..utils.basic.path_utils import pj
from ..utils.basic.misc_utils import unique, get_total_deepsize_of
from ..utils.basic.file_utils import touch_dir
from ..utils.basic.file_utils import exists_file
from ..utils.basic.file_utils import get_mtime
from ..utils.basic.debug_utils import raise_mismatch
from ..utils.basic.serialize_utils import load_json, save_json
from ..utils.basic.str_utils import is_delimiter, normalize_text, indexed_normalize_text, resolve_match_conflicts
from ..utils.basic.config_utils import CM_AHVN
from ..utils.basic.log_utils import get_logger
from ..utils.basic.progress_utils import Progress, NoProgress
from ..utils.basic.trigram_utils import trigram
from ..utils.db.sqlite_utils import SQLiteDB

logger = get_logger(__name__)

from ..ukf.base import BaseUKF
from ..klstore.base import BaseKLStore
from .base import BaseKLEngine

import sys
from typing import Any, Dict, List, Optional, Iterable, Literal, Callable, Union, Type
from collections import defaultdict


class GRAMKLEngine(BaseKLEngine):
    """\
    A hash-based sparse n-gram KLEngine for efficient string search in BaseUKF objects.

    This engine uses the a hash-based sparse n-gram algorithm for fast multi-pattern string matching.
    It's particularly useful for knowledge base applications where you need to find all occurrences
    of known entity strings within a given text query. The engine is designed to be `inplace`
    (storing only id and string, not full data) and requires external storage for BaseUKF objects.

    Search Methods:
        _search(query, conflict, whole_word, include, *args, **kwargs): Hash-based string search.

    Abstract Methods (inherited from BaseKLEngine):
        _upsert(kl): Insert or update a BaseUKF in the engine.
        _remove(key): Remove a BaseUKF from the engine by its key (id).
        _clear(): Clear all BaseUKF objects from the engine.
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
        *args,
        **kwargs,
    ):
        """\
        Initialize the GRAMKLEngine.

        Args:
            storage (BaseKLStore): The storage backend for BaseUKF objects (required).
            path (str): Local directory path to store synonyms files.
            encoder (Callable[[BaseUKF], List[str]]): Function to extract searchable strings from BaseUKF objects.
                The recommended pattern is to use lambda kl: kl.synonyms where kl.synonyms contains
                all string variants that should point to the same knowledge object.
            min_length (int): Minimum length of strings to include in the hash_table. Default is 2.
            inverse (bool): If True, builds the hash_table on reversed strings for suffix matching efficiency. Default is True.
            normalizer (Optional[Union[Callable[[str], str], bool]]): Function to normalize strings before indexing and searching.
                If True, uses a default text normalizer including tokenization, stop word removal, lemmatization, and lowercasing.
                If None or False, no normalization is applied. Default is None.
            name: Name of the KLEngine instance. If None, defaults to "{storage.name}_gram_idx".
            condition: Optional upsert/insert condition to apply to the KLEngine.
                KLs that do not satisfy the condition will be ignored. If None, all KLs are accepted.
            encoding (Optional[str]): Encoding used for saving/loading files. Default is None, which uses `CM_AHVN`'s default encoding.
            *args: Additional positional arguments passed to BaseKLEngine.
            **kwargs: Additional keyword arguments passed to BaseKLEngine.

        """
        super().__init__(storage=storage, name=name or f"{storage.name}_gram_idx", condition=condition, *args, **kwargs)

        if encoder is None:

            def default_encoder(kl: BaseUKF) -> List[str]:
                return unique([kl.name] + (list(kl.synonyms) if kl.synonyms is not None else []))

            encoder = default_encoder

        self.path = path
        self.encoder = encoder
        self.min_length = min_length
        self.inverse = inverse
        self.encoding = encoding or CM_AHVN.get("core.encoding", "utf-8")
        self.__syncing = False

        touch_dir(path)

        if (normalizer is None) or isinstance(normalizer, bool):

            def vanilla_normalizer(text: str) -> str:
                return text

            self.normalizer = normalize_text if normalizer else vanilla_normalizer
            self.indexed_normalizer = indexed_normalize_text if normalizer else None
        else:
            self.normalizer = normalizer
            self.indexed_normalizer = None

        self._clear()
        if not self.load():
            self.save()

    def __len__(self):
        """\
        Returns the number of unique BaseUKF entities (IDs) currently indexed by the engine.
        """
        return len(self.kl_synonyms)

    def _has(self, key: int) -> bool:
        """\
        Check if a BaseUKF with the given key (id) exists in the engine.

        Args:
            key (int): The ID of the BaseUKF to check.

        Returns:
            bool: True if the BaseUKF exists in the engine, False otherwise.
        """
        return key in self.kl_synonyms

    def _search(
        self,
        query: str = "",
        conflict: Literal["overlap", "longest", "longest_distinct"] = "overlap",
        whole_word: bool = False,
        include: Optional[Iterable[str]] = None,
        *args,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """\
        Perform hash-based search on the query string.

        Args:
            query (str): The text string to search within.
            conflict (Literal): Specifies the matching strategy for overlapping results.
                - "overlap": Returns all found matches, including overlapping ones.
                - "longest": Keeps only the longest match for any overlapping set.
                - "longest_distinct": Allows multiple entities to match overlapping segments
                                    as long as they are the longest matches.
            whole_word (bool): If True, only returns matches that are complete words.
            include (Optional[Iterable[str]]): The keys to include in the search results.
                Supported keys include:
                - 'id': The unique identifier of the BaseUKF (BaseUKF.id).
                - 'kl': The BaseUKF object itself (retrieved from storage).
                - 'query': The normalized query string used for searching.
                - 'matches': List of (start, end) tuples for match positions in the ORIGINAL query
                    (mapped back from normalized space when indexed normalizer is available).
                - 'strs': The matched substrings from the original query
                    (mapped back from normalized space when indexed normalizer is available).
                Defaults to None, which resolves to ['id', 'kl', 'strs'].
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, where each dictionary represents
                                a matched BaseUKF entity and its details.
        """
        if not query or len(self.kl_synonyms) == 0:
            return []

        _supported_includes = ["id", "kl", "query", "matches", "strs"]
        include_set = set(include) if include is not None else {"id", "kl", "strs"}
        for inc in include_set:
            raise_mismatch(
                _supported_includes,
                got=inc,
                name="search `include` type",
                mode="warn",
                comment="It will be ignored in the return results.",
                thres=1.0,
            )

        if self.indexed_normalizer is not None:
            normalized_query, char_origins = self.indexed_normalizer(query)
        else:
            normalized_query = self.normalizer(query)
            char_origins = None

        inv_q = f"{normalized_query}"[::-1] if self.inverse else f"{normalized_query}"

        matches = self._find_matches(inv_q, normalized_query, whole_word)

        # Build results with list comprehension - always include matches for conflict resolution
        kid_segs = list(matches.items())
        results = [
            {
                "id": int(kid),
                "matches": list(set(segs)),
            }
            for kid, segs in kid_segs
        ]

        # Apply conflict resolution using utility function (in normalized space)
        results = resolve_match_conflicts(
            results=results,
            conflict=conflict,
            query_length=len(inv_q),
            inverse=self.inverse,
        )

        # Map matches back to original query positions when indexed normalizer is available
        if char_origins:
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

        return [{k: v for k, v in result.items() if k in include_set} for result in results]

    def _find_matches(self, inv_q: str, normalized_query: str, whole_word: bool) -> Dict[str, List]:
        """\
        Find all matches using trigram hash_table.
        """
        matches = defaultdict(list)

        result = self.tri.query(inv_q)

        for inv_syn, start_idx, kids in result:
            syn = f"{inv_syn}"[::-1] if self.inverse else f"{inv_syn}"

            if self.inverse:
                start, end = (len(inv_q) - start_idx - len(syn)), (len(inv_q) - start_idx)
            else:
                start, end = start_idx, start_idx + len(syn)

            if self._is_whole_word_match(normalized_query, start, end, whole_word):
                for kid in self.synonyms[syn]:
                    matches[kid].append((start, end))

        return matches

    def _is_whole_word_match(self, normalized_query: str, start: int, end: int, whole_word: bool) -> bool:
        """\
        Check if match is a whole word.
        """
        if not whole_word:
            return True
        return is_delimiter(f" {normalized_query} "[start]) and is_delimiter(f" {normalized_query} "[end + 1])

    def _upsert(self, kl: BaseUKF, flush: bool = True, **kwargs):
        """\
        Insert or update a BaseUKF object in the engine.

        The engine extracts searchable strings from the BaseUKF using the configured
        encoder and adds them to the n-gram hash-table for indexing.

        Args:
            kl (BaseUKF): The BaseUKF object to insert or update.
            flush (bool): If True, saves the engine state after upserting the BaseUKF. Default is True.
            kwargs: Additional keyword arguments.
        """
        search_strings = self.encoder(kl)
        if not search_strings or not isinstance(search_strings, list):
            return

        strings_to_add = unique(s for s in search_strings if isinstance(s, str))
        if not strings_to_add:
            return

        for i, string in enumerate(strings_to_add):
            if len(string) < self.min_length:
                continue

            normalized_string = self.normalizer(string)
            self.synonyms[normalized_string].add(kl.id)
            if not self.__syncing:
                self.db.upsert("synonyms", {"syn": normalized_string, "kids": str(self.synonyms[normalized_string])}, conflict_columns="syn")

            inv_string = f"{normalized_string}"[::-1] if self.inverse else f"{normalized_string}"
            self.tri.add(inv_string, f"{kl.id}_{i:02d}")

        all_strings = [s for s in strings_to_add if len(s) >= self.min_length]
        self.kl_synonyms[kl.id] = all_strings

        if not self.__syncing:
            self.db.insert("kl_synonyms", {"kid": str(kl.id), "strings": str(all_strings)})
            self.load_time = get_mtime(pj(self.path, "synonyms.db"))
        else:
            self.load_time = 0

        # kl_data = self.db.select_all('kl_synonyms')
        # with open("trigram.txt","a") as file:
        #     print(kl_data,file=file)

        if flush:
            self.flush()

    def _batch_upsert(self, kls, flush: bool = True, progress: Progress = None, **kwargs):
        """\
        Insert or update multiple BaseUKF objects in the engine.

        The engine extracts searchable strings from each BaseUKF using the configured
        encoder and adds them to the trigram hash_table for indexing.

        Args:
            kls (Iterable[BaseUKF]): The BaseUKF objects to insert or update.
            flush (bool): If True, saves the engine state after upserting all BaseUKFs. Default is True.
            kwargs: Additional keyword arguments.
        """
        for kl in kls:
            self._upsert(kl, flush=False, **kwargs)
            if progress is not None:
                progress.update(1)

        if flush:
            self.flush()

    def _remove(self, key: int, flush: bool = True, **kwargs) -> bool:
        """\
        Remove a BaseUKF from the engine by its key (id).

        Args:
            key (int): The ID of the BaseUKF to remove.
            flush (bool): If True, saves the engine state after marking for deletion. Default is True.
            kwargs: Additional keyword arguments.

        Returns:
            bool: True if the BaseUKF was marked for deletion, False if it didn't exist.
        """
        if key not in self.kl_synonyms:
            return False

        strings = self.kl_synonyms[key]
        for string in strings:
            normalized_string = self.normalizer(string)
            if key not in self.synonyms[normalized_string]:
                continue
            self.synonyms[normalized_string].remove(key)
            if not self.synonyms[normalized_string]:
                self.db.delete_by_indexed("synonyms", "syn", normalized_string)
                del self.synonyms[normalized_string]
            else:
                self.db.upsert("synonyms", {"syn": normalized_string, "kids": str(self.synonyms[normalized_string])}, conflict_columns="syn")

        del self.kl_synonyms[key]
        self.db.delete_by_indexed("kl_synonyms", "kid", str(key))

        self.load_time = get_mtime(pj(self.path, "synonyms.db"))

        if flush:
            self.flush()

        return True

    def _batch_remove(self, keys: Iterable[int], flush: bool = True, progress: Progress = None, **kwargs):
        """\
        Remove multiple BaseUKF objects from the engine by their keys (ids).

        Args:
            keys (Iterable[int]): The IDs of the BaseUKFs to remove.
            flush (bool): If True, saves the engine state after marking for deletion. Default is True.
            kwargs: Additional keyword arguments.
        """
        keys = unique(keys)  # Keeping only unique keys
        if not keys:
            return

        for key in keys:
            if key not in self.kl_synonyms:
                continue
            self._remove(key, flush=False, **kwargs)
            if progress is not None:
                progress.update(1)

        if flush:
            self.flush()

    def db_create(self, path: str):

        # with open("trigram.txt","a") as file:
        #     print(path,file=file)

        self.db = SQLiteDB(path, logger=logger)
        # self.db.drop_all_table()
        self.db.create_table("synonyms", {"id": "INTEGER PRIMARY KEY AUTOINCREMENT", "syn": "TEXT UNIQUE NOT NULL", "kids": "TEXT NOT NULL"})
        self.db.create_index("synonyms", "idx_syn", "syn", unique=True)
        self.db.create_table("kl_synonyms", {"id": "INTEGER PRIMARY KEY AUTOINCREMENT", "kid": "TEXT UNIQUE NOT NULL", "strings": "TEXT NOT NULL"})
        self.db.create_index("kl_synonyms", "idx_kid", "kid", unique=True)

    def _clear(self):
        """\
        Clear all BaseUKF objects from the engine, resetting it to an empty state.
        """
        self.tri = trigram()
        self.load_time = 0
        self.synonyms = defaultdict(set)
        self.kl_synonyms = dict()
        self.db_create(pj(self.path, "synonyms.db"))

    def clear(self, **kwargs):
        """\
        Clear all BaseUKF objects from the engine, resetting it to an empty state.
        """
        self._clear()
        self.flush()

    def flush(self):
        self.save()

    def sync(self, batch_size: Optional[int] = None, flush: bool = True, progress: Type[Progress] = None, **kwargs):
        """\
        Synchronize KLEngine with its attached KLStore, if applicable.
        Notice that a whole synchronization can often lead to large data upload/download.
        This could result in performance issues and even errors for particular backends.
        Therefore, parameters like batch_size are provided to control the synchronization process.
        It is recommended to override this method for better performance.

        Args:
            batch_size (Optional[int]): The batch size for synchronization.
                If None, use the default batch size from configuration (512).
                If <= 0, yields all KLs in a single batch.
            flush (bool): If True, saves the engine state after synchronization. Default is True.
            progress (Type[Progress]): Progress class for reporting. None for silent, TqdmProgress for terminal.
            **kwargs: Additional keyword arguments.
        """
        self.clear()  # Remove all existing KLs for synchronization
        self.__syncing = True  # Set syncing flag to avoid redundant DB operations during batch upsert
        batch_size = batch_size or CM_AHVN.get("klengine.sync_batch_size", 512)
        num_kls = len(self.storage)
        total = num_kls
        batch_iter = self.storage.batch_iter(batch_size=batch_size)
        progress_cls = progress or NoProgress
        with progress_cls(total=total, desc=f"Syncing KLEngine '{self.name}'") as pbar:
            for kl_batch in batch_iter:
                self.batch_upsert(kl_batch, flush=False, progress=None, **kwargs)
                pbar.update(len(kl_batch))
        self.__syncing = False  # Reset syncing flag after batch upsert
        if flush:
            self.flush()

    def save(self, path: str = None):
        """\
        Save the current state of the engine to disk.

        Args:
            path (str): Directory path to save the data. If None, uses self.path.
        """

        # with open("trigram.txt","a") as file:
        #     print("SAVE",self.__len__(),file=file)

        if path is None:
            path = self.path

        if path == self.path and self.load_time == get_mtime(pj(path, "synonyms.db")):
            # Avoid unnecessary save if nothing has changed since last load
            return

        self.path = path
        self.db_create(pj(path, "synonyms.db"))
        self.db.delete_all("kl_synonyms")
        self.db.delete_all("synonyms")

        # self.tri.save(pj(path, "ngram.json"))
        # save 中 hash_table 的 dict 存储到文件太慢了，改用load时重构

        kl_data = [{"kid": str(kid), "strings": str(strings)} for kid, strings in self.kl_synonyms.items()]
        self.db.insert_many("kl_synonyms", kl_data)
        syn_data = [{"syn": syn, "kids": str(kids)} for syn, kids in self.synonyms.items()]
        self.db.insert_many("synonyms", syn_data)

        metadata = {"min_length": self.min_length, "inverse": self.inverse}
        save_json(metadata, pj(path, "metadata.json"), encoding=self.encoding)

    def load(self, path: str = None) -> bool:
        """\
        Load a previously saved engine state from disk.

        Args:
            path (str): Directory path to load the data from. If None, uses self.path.

        Returns:
            bool: True if loading was successful (files exist), False otherwise.
        """

        if path is None:
            path = self.path

        # with open("trigram.txt","a") as file:
        #     print("LOAD",path,file=file)

        required_files = ["synonyms.db", "metadata.json"]
        if not all(exists_file(pj(path, f)) for f in required_files):
            return False

        load_time = get_mtime(pj(path, "synonyms.db"))
        if self.load_time == load_time:
            return False

        self._clear()  # Clear current state before loading new data
        self.db = SQLiteDB(pj(path, "synonyms.db"), logger=logger)
        self.load_time = load_time

        metadata = load_json(pj(path, "metadata.json"), encoding=self.encoding)
        self.min_length = metadata.get("min_length", 2)
        self.inverse = metadata.get("inverse", True)

        kl_data = self.db.select_all("kl_synonyms")
        for row in kl_data:
            self.kl_synonyms[int(row["kid"])] = eval(row["strings"])

        syn_data = self.db.select_all("synonyms")
        for row in syn_data:
            self.synonyms[row["syn"]] = eval(row["kids"])

        for k, v in self.kl_synonyms.items():
            for i, string in enumerate(v):
                normalized_string = self.normalizer(string)
                inv_string = f"{normalized_string}"[::-1] if self.inverse else f"{normalized_string}"
                self.tri.add(inv_string, f"{k}_{i:02d}")

        # self.tri = trigram.load(pj(path, "ngram.json"))

        return True

    def __sizeof__(self) -> int:
        return sys.getsizeof(self.tri) + get_total_deepsize_of(self.synonyms, self.kl_synonyms)

    def getsizeof(self) -> int:
        return self.__sizeof__()

    def close(self):
        """\
        Close the engine and save current state to disk.
        """
        self.flush()
        self._clear()
        self.db.close()
