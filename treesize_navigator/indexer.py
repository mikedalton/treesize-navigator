from __future__ import annotations

import os
from collections import defaultdict
from typing import Callable, NamedTuple


def format_size(n: int) -> str:
    for unit, threshold in [("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)]:
        if n >= threshold:
            return f"{n / threshold:.2f} {unit}"
    return f"{n} B"


class ChildEntry(NamedTuple):
    name: str
    total_bytes: int
    is_dir: bool


class FileIndex:
    """
    Builds a complete directory-size tree from a find -ls snapshot.

    Single streaming pass: each entry's size is propagated up to all ancestor
    directories, so subtree totals are ready for any path without re-scanning.
    """

    def __init__(self) -> None:
        self._subtree_bytes: dict[str, int] = defaultdict(int)
        self._children: dict[str, set[str]] = defaultdict(set)
        self._root: str = ""
        self._root_depth: int = 999_999
        self.entry_count: int = 0

    @classmethod
    def from_file(
        cls,
        path: str,
        progress_cb: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> "FileIndex | None":
        index = cls()
        file_size = os.path.getsize(path)
        bytes_read = 0
        with open(path, encoding="utf-8", errors="replace", buffering=1 << 20) as fh:
            for line in fh:
                bytes_read += len(line)
                parts = line.split()
                if len(parts) < 11:
                    continue
                try:
                    size = int(parts[6])
                except ValueError:
                    continue
                # Join from col 11 onward to handle paths containing spaces
                filepath = " ".join(parts[10:])
                index._add_entry(filepath, size)
                if index.entry_count % 50_000 == 0:
                    if cancelled and cancelled():
                        return None
                    if progress_cb:
                        progress_cb(bytes_read, file_size)
        if progress_cb:
            progress_cb(file_size, file_size)
        return index

    def _add_entry(self, path: str, size: int) -> None:
        path = path.rstrip("/")
        if not path:
            return

        self.entry_count += 1
        parts = path.split("/")

        # Track the shallowest entry seen — that becomes the navigation root.
        depth = len(parts)
        if depth < self._root_depth:
            self._root_depth = depth
            self._root = path

        # Propagate size to all ancestors (builds subtree totals in one pass).
        for i in range(len(parts), 0, -1):
            ancestor = "/".join(parts[:i]) or "/"
            self._subtree_bytes[ancestor] += size

        # Record the parent→child relationship for navigation.
        if len(parts) > 1:
            parent = "/".join(parts[:-1]) or "/"
            self._children[parent].add(parts[-1])

    @property
    def root(self) -> str:
        return self._root if self._root else "/"

    def get_children(self, dir_path: str) -> list[ChildEntry]:
        dir_path = dir_path.rstrip("/") or "/"
        prefix = ("/" if dir_path == "/" else dir_path) + "/"
        result = []
        for child_name in self._children.get(dir_path, ()):
            child_path = prefix + child_name
            child_bytes = self._subtree_bytes.get(child_path, 0)
            is_dir = child_path in self._children
            result.append(ChildEntry(child_name, child_bytes, is_dir))
        return result

    def get_total(self, dir_path: str) -> int:
        return self._subtree_bytes.get(dir_path.rstrip("/") or "/", 0)

    def has_children(self, dir_path: str) -> bool:
        return dir_path in self._children
