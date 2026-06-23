__all__ = [
    "unique",
    "lflat",
    "lreshape",
    "counter_percentiles",
    "get_deepsize_of",
    "get_total_deepsize_of",
]

import math
import sys
from typing import Iterable, List, Any, Dict, Union, Set, Tuple


def unique(iterable: Iterable[Any], key=lambda x: x) -> List[Any]:
    """\
    Return a list of unique elements from an iterable, preserving order.

    Args:
        iterable (Iterable[Any]): The input iterable to filter for unique elements.
        key (callable, optional): A function to extract a comparison key from each element. Defaults to the identity function.

    Returns:
        List[Any]: A list containing only the unique elements from the input iterable.

    Examples:
        >>> unique([1, 2, 2, 3, 1])
        [1, 2, 3]
        >>> unique(['apple', 'banana', 'orange'], key=len)
        ['apple', 'banana']
    """
    seen, uni = set(), list()
    for item in iterable:
        k = key(item)
        if not (k in seen or seen.add(k)):
            uni.append(item)
    return uni


def lflat(iterable: Iterable[Iterable[Any]]) -> List[Any]:
    """\
    Flatten a nested iterable (2 levels deep) into a single list.

    Args:
        iterable (Iterable[Iterable[Any]]): The nested iterable to flatten.

    Returns:
        List[Any]: A flat list containing all elements from the nested iterable.

    Examples:
        >>> lflat([[1, 2], [3, 4]])
        [1, 2, 3, 4]
    """
    return [item for sublist in iterable for item in sublist]


def lreshape(iterable: List[Any], shape: Tuple[int, ...]) -> Union[List[Any], List[List[Any]]]:
    """\
    Reshape a flat list into a nested list structure based on the given shape.

    This is a pure Python alternative to np.array().reshape() that works with
    non-trivial objects that NumPy can't handle.

    Args:
        iterable: A flat list of items to reshape.
        shape: The target shape as a tuple of dimensions. The product of all
                dimensions must equal the length of flat_list.

    Returns:
        A nested list structure matching the requested shape.

    Example:
        >>> list_reshape([1, 2, 3, 4, 5, 6], (2, 3))
        [[1, 2, 3], [4, 5, 6]]
        >>> list_reshape([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], (2, 2, 3))
        [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]
    """
    if math.prod(shape) != len(iterable):
        raise ValueError(f"Cannot reshape list of size {len(iterable)} into shape {shape}")
    if len(shape) == 1:
        return list(iterable)
    data = list(iterable)
    outer_dim = shape[0]
    inner_size = len(data) // outer_dim
    inner_shape = shape[1:]
    result = []
    for i in range(outer_dim):
        start = i * inner_size
        end = start + inner_size
        slice_list = data[start:end]
        if len(inner_shape) == 1:
            result.append(slice_list)
        else:
            result.append(lreshape(slice_list, inner_shape))
    return result


def counter_percentiles(counter: Dict, percentiles: Set[Union[int, float]] = {0, 25, 50, 75, 100}) -> Dict[int, Any]:
    """\
    Calculate specified percentiles from a frequency counter.

    Args:
        counter (Dict): A dictionary where keys are values and values are their frequencies.
        percentiles (List[Union[int, float]]): List of percentiles to calculate. Defaults to [0, 25, 50, 75, 100].

    Returns:
        Dict[int, Any]: A dictionary mapping each requested percentile to its corresponding value.

    Examples:
        >>> counter = {1: 2, 2: 3, 3: 5}
        >>> counter_percentiles(counter, [0, 50, 100])
        {0: 1, 50: 2, 100: 3}
    """
    total = sum(counter.values())
    sorted_items = sorted(counter.items(), key=lambda x: x[0])
    results, accum, idx = [(p, None) for p in sorted(percentiles)], 0, 0
    for value, freq in sorted_items:
        accum += freq
        while idx < len(results) and accum / total >= results[idx][0] / 100.0:
            results[idx] = (results[idx][0], value)
            idx += 1
    return dict(results)


def get_deepsize_of(obj: Any, seen: Set[int] = None) -> int:
    """\
    Approximate deep memory size.

    This function traverses nested containers (dict, list, set, tuple, frozenset) and sums the
    memory sizes of all contained objects, while correctly handling circular references.

    Limitations:
    - Does NOT calculate memory of objects accessed via __slots__ or __dict__
    - May double-count or under-count shared objects depending on perspective
    - Ignores interpreter overhead (reference counts, GC headers)
    - Not suitable for production memory profiling

    Args:
        obj (Any): The object whose deep memory usage is to be calculated.
        seen (Set[int], optional): A set of object IDs already processed. Used internally
            to detect and avoid circular references. Defaults to None.

    Returns:
        int: Total memory size in bytes consumed by the object and all its referenced objects.

    Examples:
        >>> data = {"a": [1, 2, 3], "b": {"x": 10, "y": 20}}
        >>> get_deepsize_of(data)
        764

        >>> # Circular reference detection
        >>> circular_list = []
        >>> circular_list.append(circular_list)
        >>> get_deepsize_of(circular_list)  # Does not enter infinite loop
        88
    """
    if seen is None:
        seen = set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)

    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(get_deepsize_of(k, seen) + get_deepsize_of(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, set, tuple, frozenset)):
        size += sum(get_deepsize_of(item, seen) for item in obj)

    return size


def get_total_deepsize_of(*objs: Any) -> int:
    """\
    Approximate total deep memory size of multiple objects, ensuring shared references are counted only once.

    Args:
        *objs: Variable number of objects whose combined deep memory size is to be calculated.

    Returns:
        int: Total memory size in bytes occupied by all objects and their referenced sub-objects,
             with shared references counted only once.

    Examples:
        >>> # Example output vary by Python version

        >>> # Simple objects
        >>> get_total_deepsize_of(42, "hello", [1, 2, 3])
        246

        >>> # Complex nested structures
        >>> data1 = {"a": [1, 2], "b": {"x": 10}}
        >>> data2 = [1, 2, 3, 4, 5]
        >>> get_total_deepsize_of(data1, data2)
        890

        >>> # Empty arguments
        >>> get_total_deepsize_of()
        0
    """
    seen = set()
    return sum(get_deepsize_of(obj, seen) for obj in objs)
