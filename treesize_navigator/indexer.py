from __future__ import annotations

import os
import pickle
import sys
from collections import defaultdict
from typing import Callable, NamedTuple

CACHE_VERSION = 1
_PROGRESS_STEP = 1 << 20  # report cache-load progress every 1 MB read


class _ProgressReader:
    """Wraps a binary file to emit byte-level read progress to a callback."""

    def __init__(
        self, f: object, total: int, cb: Callable[[int, int], None]
    ) -> None:
        self._f = f
        self._total = total
        self._read = 0
        self._last = 0
        self._cb = cb

    def _tick(self, n: int) -> None:
        self._read += n
        if self._read - self._last >= _PROGRESS_STEP:
            self._last = self._read
            self._cb(self._read, self._total)

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)  # type: ignore[union-attr]
        self._tick(len(data))
        return data

    def readline(self) -> bytes:
        data = self._f.readline()  # type: ignore[union-attr]
        self._tick(len(data))
        return data

    def readinto(self, buf: bytearray) -> int:
        n = self._f.readinto(buf)  # type: ignore[union-attr]
        self._tick(n)
        return n


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
                parts = line.split(None, 10)
                if len(parts) < 11:
                    continue
                try:
                    size = int(parts[6])
                except ValueError:
                    continue
                filepath = parts[10].rstrip()
                index._add_entry(filepath, size)
                if index.entry_count % 50_000 == 0:
                    if cancelled and cancelled():
                        return None
                    if progress_cb:
                        progress_cb(bytes_read, file_size)
        if progress_cb:
            progress_cb(file_size, file_size)
        return index

    @classmethod
    def from_cache(
        cls,
        cache_path: str,
        source_path: str,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> "FileIndex | None":
        """Load a previously saved index; returns None if cache is missing or stale."""
        try:
            source_stat = os.stat(source_path)
            cache_size = os.path.getsize(cache_path)
            with open(cache_path, "rb") as f:
                reader = (
                    _ProgressReader(f, cache_size, progress_cb) if progress_cb else f
                )
                header, data = pickle.load(reader)  # type: ignore[arg-type]
            version, stored_path, stored_mtime, stored_size = header
            if (
                version != CACHE_VERSION
                or stored_path != os.path.abspath(source_path)
                or stored_mtime != source_stat.st_mtime
                or stored_size != source_stat.st_size
            ):
                return None
            index = cls()
            (
                index._subtree_bytes,
                index._children,
                index._root,
                index._root_depth,
                index.entry_count,
            ) = data
            return index
        except Exception:
            return None

    def save_cache(self, cache_path: str, source_path: str) -> None:
        """Persist this index to disk for fast reloading."""
        source_stat = os.stat(source_path)
        header = (
            CACHE_VERSION,
            os.path.abspath(source_path),
            source_stat.st_mtime,
            source_stat.st_size,
        )
        data = (
            self._subtree_bytes,
            self._children,
            self._root,
            self._root_depth,
            self.entry_count,
        )
        with open(cache_path, "wb") as f:
            pickle.dump((header, data), f, protocol=5)

    def _add_entry(self, path: str, size: int) -> None:
        path = path.rstrip("/")
        if not path:
            return

        self.entry_count += 1

        # Track the shallowest entry seen — that becomes the navigation root.
        depth = path.count("/") + 1
        if depth < self._root_depth:
            self._root_depth = depth
            self._root = path

        # Propagate size to this path and all ancestors via rfind walk.
        # sys.intern shares string objects across both dicts for common dir paths.
        s = path
        while True:
            self._subtree_bytes[sys.intern(s)] += size
            idx = s.rfind("/")
            if idx <= 0:
                if idx == 0:
                    self._subtree_bytes["/"] += size
                break
            s = s[:idx]

        # Record parent→child relationship for navigation.
        idx = path.rfind("/")
        if idx > 0:
            self._children[sys.intern(path[:idx])].add(path[idx + 1:])
        elif idx == 0:
            self._children["/"].add(path[1:])

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
