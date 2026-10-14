"""Atomic file writes: write to a temporary file in the same folder, then rename over the target.

A run interrupted mid-write leaves the previous file (or none), never a half-written stage output, so resume can
trust what is on disk.
"""

import os
from pathlib import Path


def _tmp(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_name(f".{path.name}.tmp-{os.getpid()}")


def write_text(path: Path, text: str) -> None:
    tmp = _tmp(path)
    tmp.write_text(text)
    os.replace(tmp, path)


def write_bytes(path: Path, data: bytes) -> None:
    tmp = _tmp(path)
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_with(path: Path, save) -> None:
    """For writers that take a path (python-docx's Document.save)."""
    tmp = _tmp(path)
    save(tmp)
    os.replace(tmp, path)
