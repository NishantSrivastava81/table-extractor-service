"""Filesystem blob store.

Keys are built from the job id only, never from a client-supplied filename, so path traversal
is not merely filtered but structurally impossible. The resolve check below is a second line of
defence in case a future caller constructs a key differently.
"""

from __future__ import annotations

import shutil
from pathlib import Path


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"blob key escapes the store root: {key!r}")
        return candidate

    def ping(self) -> None:
        if not self._root.is_dir():
            raise RuntimeError(f"blob root missing: {self._root}")

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        target = self._path(prefix)
        if not target.is_dir():
            return 0
        count = sum(1 for _ in target.rglob("*") if _.is_file())
        shutil.rmtree(target, ignore_errors=True)
        return count
