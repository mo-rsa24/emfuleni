"""Inbox adapters — sources of unprocessed extract files.

Production swaps in `SftpInboxAdapter` (talks to the municipality's SFTP
drop). Dev uses `FileSystemInboxAdapter` (a local directory). Both share
the same interface so the rest of ingest does not care which is wired in.

A per-tenant inbox convention (subdir per municipality.slug under the
configured root) keeps tenancy explicit at the filesystem boundary — no
risk of cross-tenant file leakage at the importer layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from django.conf import settings

from common.models import Municipality
from .models import Extract


@dataclass(frozen=True)
class InboxFile:
    """A file the inbox has surfaced. Immutable handle."""

    path: Path
    filename: str


class InboxAdapter(Protocol):
    def list_new_files(self, municipality: Municipality) -> Iterable[InboxFile]:
        """Return files in the inbox that have not yet been imported.

        Implementations must NOT mark files as processed; the importer
        commits an `Extract` row once import succeeds, and the next call
        skips files whose `content_hash` already matches an `Extract`
        under this tenant.
        """
        ...


class FileSystemInboxAdapter:
    """Inbox backed by a directory on disk.

    Layout: `<root>/<municipality_slug>/*.csv`. Files appear when someone
    drops them in. Idempotency is delegated to the importer's hash check,
    so re-scanning the same directory is safe.
    """

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else Path(settings.INGEST_INBOX_DIR)

    def tenant_dir(self, municipality: Municipality) -> Path:
        return self.root / municipality.slug

    def list_new_files(self, municipality: Municipality) -> list[InboxFile]:
        tenant_dir = self.tenant_dir(municipality)
        if not tenant_dir.is_dir():
            return []

        seen_hashes = set(
            Extract.objects.for_tenant(municipality).values_list(
                "content_hash", flat=True
            )
        )

        results: list[InboxFile] = []
        for path in sorted(tenant_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            content_hash = _hash_file(path)
            if content_hash in seen_hashes:
                continue
            results.append(InboxFile(path=path, filename=path.name))
        return results


def _hash_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_file(path: Path) -> str:
    """Public alias — the importer also needs to compute hashes."""
    return _hash_file(path)
