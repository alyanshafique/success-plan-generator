"""Filesystem layout for per-company data and outputs."""

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot build a directory name from company {name!r}")
    return slug


@dataclass(frozen=True)
class CompanyPaths:
    root: Path
    slug: str

    @classmethod
    def for_company(cls, company: str, root: Path = PROJECT_ROOT) -> "CompanyPaths":
        return cls(root=root, slug=slugify(company))

    @property
    def data(self) -> Path:
        return self.root / "data" / self.slug

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def stages(self) -> Path:
        return self.data / "stages"

    @property
    def chunks_file(self) -> Path:
        return self.raw / "chunks.jsonl"

    @property
    def shared_cache(self) -> Path:
        return self.root / "data" / "_cache"
