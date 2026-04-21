"""Pluggable object/file storage.

Abstracts the few operations connectors (and future modules) need from
the filesystem so we can swap the backend without touching service code:

    * ``LocalFilesystemStorage`` — current default, writes under
      ``MEDIA_ROOT`` and serves via FastAPI ``StaticFiles`` at
      ``MEDIA_URL_PREFIX``.
    * ``S3Storage`` — stub. When you're ready to move to S3/CloudFront,
      install ``boto3`` and fill in the four ``NotImplementedError`` hooks
      (``save``, ``delete``, ``exists``, ``move``). The URL helpers
      (``url_for``/``key_from_url``) already work so existing rows stay
      addressable.

The active backend is chosen at process start via the ``STORAGE_BACKEND``
setting (``local`` or ``s3``). Tests can override it with
``set_storage(...)``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Storage(ABC):
    """Minimal storage interface used by the connector module."""

    @abstractmethod
    def save(self, *, key: str, data: bytes, content_type: str | None = None) -> str:
        """Persist ``data`` at ``key`` and return the public URL."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object at ``key``. No-op if it doesn't exist."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True iff an object exists at ``key``."""

    @abstractmethod
    def move(self, *, src_key: str, dst_key: str) -> None:
        """Rename/move an object. No-op if ``src_key`` is missing."""

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """Return keys whose names start with ``prefix``."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """Build the public URL that resolves to the object at ``key``."""

    @abstractmethod
    def key_from_url(self, url: str | None) -> str | None:
        """Reverse of ``url_for``; ``None`` for external/foreign URLs."""


class LocalFilesystemStorage(Storage):
    """Writes to ``root`` and serves at ``url_prefix``.

    Only paths that resolve inside ``root`` are accepted; traversal attempts
    raise ``ValueError`` from the internal helper and are treated as missing
    by the public methods.
    """

    def __init__(self, *, root: str | Path, url_prefix: str) -> None:
        self.root = Path(root).resolve()
        self.url_prefix = ("/" + url_prefix.strip("/")) if url_prefix else "/media"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        clean = key.replace("\\", "/").lstrip("/")
        if not clean or ".." in clean.split("/"):
            raise ValueError("invalid key")
        path = (self.root / clean).resolve()
        path.relative_to(self.root)  # raises ValueError on escape
        return path

    def save(self, *, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.url_for(key)

    def delete(self, key: str) -> None:
        try:
            path = self._path(key)
        except ValueError:
            return
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def move(self, *, src_key: str, dst_key: str) -> None:
        try:
            src = self._path(src_key)
            dst = self._path(dst_key)
        except ValueError:
            return
        if not src.is_file() or src == dst:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dst.exists():
                dst.unlink()
            src.rename(dst)
        except OSError:
            pass

    def list_prefix(self, prefix: str) -> list[str]:
        try:
            base = self._path(prefix) if "/" in prefix else self.root / prefix
        except ValueError:
            return []
        parent = base.parent if base.parent != self.root.parent else self.root
        if not parent.is_dir():
            return []
        out: list[str] = []
        for p in parent.glob(base.name + "*"):
            if p.is_file():
                rel = p.relative_to(self.root).as_posix()
                out.append(rel)
        return out

    def url_for(self, key: str) -> str:
        return f"{self.url_prefix}/{key.lstrip('/')}"

    def key_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        prefix = self.url_prefix + "/"
        if url.startswith(prefix):
            return url[len(prefix):]
        return None


class S3Storage(Storage):
    """S3-backed storage.

    Stores objects under ``s3://{bucket}/{key_prefix}/{key}`` and serves
    them either at the bucket's REST URL or, when ``url_base`` is set, at
    a CloudFront/custom-domain URL. The ``logo_sha256`` dedup column on
    ``connectors`` is unaffected by the move — hashes are computed from
    upload bytes, completely independent of where the file lives.

    AWS credentials are resolved by ``boto3`` from the environment / IAM
    role on the host (``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY`` or
    instance profile). Nothing app-specific is required beyond bucket +
    region + (optional) public URL base + key prefix.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str | None = None,
        url_base: str | None = None,
        key_prefix: str = "",
        client: object | None = None,
    ) -> None:
        self.bucket = bucket
        self.region = region
        host = url_base or (
            f"https://{bucket}.s3.{region}.amazonaws.com"
            if region
            else f"https://{bucket}.s3.amazonaws.com"
        )
        self.url_base = host.rstrip("/")
        self.key_prefix = key_prefix.strip("/")
        self._client = client  # lazy-built on first use when None

    def _get_client(self):
        if self._client is None:
            import boto3  # local import so the dependency is optional

            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def _full(self, key: str) -> str:
        k = key.lstrip("/")
        return f"{self.key_prefix}/{k}" if self.key_prefix else k

    def save(self, *, key: str, data: bytes, content_type: str | None = None) -> str:
        extra: dict[str, object] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._get_client().put_object(
            Bucket=self.bucket, Key=self._full(key), Body=data, **extra
        )
        return self.url_for(key)

    def delete(self, key: str) -> None:
        # delete_object is idempotent on S3 — no error if the key is absent.
        self._get_client().delete_object(Bucket=self.bucket, Key=self._full(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._get_client().head_object(Bucket=self.bucket, Key=self._full(key))
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def move(self, *, src_key: str, dst_key: str) -> None:
        from botocore.exceptions import ClientError

        if src_key == dst_key:
            return
        client = self._get_client()
        src_full = self._full(src_key)
        dst_full = self._full(dst_key)
        try:
            client.copy_object(
                Bucket=self.bucket,
                Key=dst_full,
                CopySource={"Bucket": self.bucket, "Key": src_full},
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey", "NotFound"):
                return
            raise
        try:
            client.delete_object(Bucket=self.bucket, Key=src_full)
        except ClientError:
            # best-effort: copy succeeded, cleanup of src can be retried later
            pass

    def list_prefix(self, prefix: str) -> list[str]:
        full = self._full(prefix)
        paginator = self._get_client().get_paginator("list_objects_v2")
        out: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
            for obj in page.get("Contents") or []:
                full_key = obj["Key"]
                if self.key_prefix and full_key.startswith(self.key_prefix + "/"):
                    out.append(full_key[len(self.key_prefix) + 1:])
                else:
                    out.append(full_key)
        return out

    def url_for(self, key: str) -> str:
        return f"{self.url_base}/{self._full(key)}"

    def key_from_url(self, url: str | None) -> str | None:
        if not url or not url.startswith(self.url_base + "/"):
            return None
        full = url[len(self.url_base) + 1:]
        if self.key_prefix and full.startswith(self.key_prefix + "/"):
            return full[len(self.key_prefix) + 1:]
        return full


def _build_storage() -> Storage:
    from app.core.config import settings

    backend = (settings.storage_backend or "local").lower()
    if backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError(
                "STORAGE_BACKEND=s3 requires S3_BUCKET to be configured"
            )
        return S3Storage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            url_base=settings.s3_url_base,
            key_prefix=settings.s3_key_prefix or "",
        )
    return LocalFilesystemStorage(
        root=settings.media_root, url_prefix=settings.media_url_prefix
    )


_override: Storage | None = None


def get_storage() -> Storage:
    """Return the active storage instance.

    Rebuilds from current settings on every call so that test monkeypatches
    of ``settings.media_root`` (or backend selection) take effect without
    needing to clear a cache. Use :func:`set_storage` to pin a specific
    instance — e.g. for stubbing S3 in tests.
    """
    if _override is not None:
        return _override
    return _build_storage()


def set_storage(storage: Storage | None) -> None:
    """Pin the active storage backend; pass ``None`` to revert to settings."""
    global _override
    _override = storage
