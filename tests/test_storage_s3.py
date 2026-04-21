"""Tests for ``S3Storage`` using a fake ``boto3`` client.

No network or AWS credentials are required: every test injects an
in-memory fake into ``S3Storage(client=...)`` to verify the right
``put_object`` / ``delete_object`` / ``copy_object`` / ``head_object`` /
``list_objects_v2`` calls happen with the right keys.
"""
from __future__ import annotations

import pytest


class FakeS3Client:
    """Minimal in-memory stand-in for the boto3 S3 client."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def _record(self, name: str, **kw):
        self.calls.append((name, kw))

    def put_object(self, *, Bucket, Key, Body, **kw):
        self._record("put_object", Bucket=Bucket, Key=Key, **kw)
        self.objects[(Bucket, Key)] = {"Body": Body, **kw}

    def delete_object(self, *, Bucket, Key):
        self._record("delete_object", Bucket=Bucket, Key=Key)
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket, Key):
        self._record("head_object", Bucket=Bucket, Key=Key)
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "HeadObject",
            )
        return {"ContentLength": len(self.objects[(Bucket, Key)]["Body"])}

    def copy_object(self, *, Bucket, Key, CopySource):
        self._record("copy_object", Bucket=Bucket, Key=Key, CopySource=CopySource)
        src = (CopySource["Bucket"], CopySource["Key"])
        if src not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}}, "CopyObject"
            )
        self.objects[(Bucket, Key)] = dict(self.objects[src])

    def get_paginator(self, name):
        client = self
        if name != "list_objects_v2":
            raise ValueError(name)

        class Pager:
            def paginate(self, *, Bucket, Prefix):
                contents = [
                    {"Key": k}
                    for (b, k) in client.objects
                    if b == Bucket and k.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return Pager()


def _make(client=None, *, key_prefix=""):
    from app.core.storage import S3Storage

    return S3Storage(
        bucket="test-bucket",
        region="us-east-1",
        key_prefix=key_prefix,
        client=client or FakeS3Client(),
    )


def test_s3_save_uploads_with_content_type():
    fake = FakeS3Client()
    s = _make(fake)
    url = s.save(
        key="connectors/foo.png", data=b"png-bytes", content_type="image/png"
    )
    assert url == "https://test-bucket.s3.us-east-1.amazonaws.com/connectors/foo.png"
    name, kw = fake.calls[0]
    assert name == "put_object"
    assert kw == {"Bucket": "test-bucket", "Key": "connectors/foo.png", "ContentType": "image/png"}
    assert fake.objects[("test-bucket", "connectors/foo.png")]["Body"] == b"png-bytes"


def test_s3_save_omits_content_type_when_none():
    fake = FakeS3Client()
    s = _make(fake)
    s.save(key="connectors/x.bin", data=b"x")
    _, kw = fake.calls[0]
    assert "ContentType" not in kw


def test_s3_save_applies_key_prefix():
    fake = FakeS3Client()
    s = _make(fake, key_prefix="prod/v1")
    url = s.save(key="connectors/a.png", data=b"x")
    assert ("test-bucket", "prod/v1/connectors/a.png") in fake.objects
    assert url.endswith("/prod/v1/connectors/a.png")


def test_s3_delete_calls_delete_object():
    fake = FakeS3Client()
    s = _make(fake)
    s.save(key="k.png", data=b"x")
    s.delete("k.png")
    assert ("test-bucket", "k.png") not in fake.objects
    assert any(name == "delete_object" for name, _ in fake.calls)


def test_s3_exists_true_and_false():
    fake = FakeS3Client()
    s = _make(fake)
    s.save(key="k.png", data=b"x")
    assert s.exists("k.png") is True
    assert s.exists("missing.png") is False


def test_s3_exists_propagates_unexpected_errors():
    from botocore.exceptions import ClientError

    class BoomClient(FakeS3Client):
        def head_object(self, *, Bucket, Key):
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}}, "HeadObject"
            )

    s = _make(BoomClient())
    with pytest.raises(ClientError):
        s.exists("k.png")


def test_s3_move_copies_then_deletes():
    fake = FakeS3Client()
    s = _make(fake)
    s.save(key="old.png", data=b"x")
    s.move(src_key="old.png", dst_key="new.png")
    assert ("test-bucket", "new.png") in fake.objects
    assert ("test-bucket", "old.png") not in fake.objects


def test_s3_move_noop_when_src_missing():
    fake = FakeS3Client()
    s = _make(fake)
    s.move(src_key="missing.png", dst_key="dest.png")
    assert ("test-bucket", "dest.png") not in fake.objects


def test_s3_move_noop_when_src_equals_dst():
    fake = FakeS3Client()
    s = _make(fake)
    s.save(key="k.png", data=b"x")
    fake.calls.clear()
    s.move(src_key="k.png", dst_key="k.png")
    assert fake.calls == []  # nothing called


def test_s3_list_prefix_strips_key_prefix():
    fake = FakeS3Client()
    s = _make(fake, key_prefix="prod")
    s.save(key="connectors/a.png", data=b"1")
    s.save(key="connectors/a.svg", data=b"2")
    s.save(key="connectors/other.png", data=b"3")
    keys = sorted(s.list_prefix("connectors/a."))
    assert keys == ["connectors/a.png", "connectors/a.svg"]


def test_s3_url_for_with_custom_url_base():
    from app.core.storage import S3Storage

    s = S3Storage(
        bucket="b",
        region="us-east-1",
        url_base="https://cdn.example.com",
        key_prefix="prod",
        client=FakeS3Client(),
    )
    assert s.url_for("connectors/a.png") == "https://cdn.example.com/prod/connectors/a.png"
    assert s.key_from_url("https://cdn.example.com/prod/connectors/a.png") == "connectors/a.png"


def test_s3_save_returns_url_pointing_at_url_base_not_bucket():
    """When url_base is set (CloudFront), save() must return that URL,
    even though put_object goes to the bucket."""
    from app.core.storage import S3Storage

    fake = FakeS3Client()
    s = S3Storage(
        bucket="my-bucket",
        region="us-east-1",
        url_base="https://cdn.example.com",
        client=fake,
    )
    url = s.save(key="connectors/x.png", data=b"x", content_type="image/png")
    assert url == "https://cdn.example.com/connectors/x.png"
    assert ("my-bucket", "connectors/x.png") in fake.objects


# --- end-to-end: connector logo upload through S3 backend ---


def test_save_connector_logo_writes_to_s3_and_records_hash(monkeypatch):
    """Wire S3Storage in as the active backend, upload a real PNG, and
    confirm logo_url is the bucket URL and logo_sha256 is set."""
    from unittest.mock import MagicMock

    from app.core.storage import S3Storage, set_storage
    from app.modules.admin.connectors import service as svc

    fake = FakeS3Client()
    s3 = S3Storage(bucket="rl-test", region="us-east-1", client=fake)
    set_storage(s3)
    monkeypatch.setattr(svc, "_assert_unique_logo_hash", lambda *a, **kw: None)

    # Build a real PNG so Pillow validation passes.
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (1, 1), (0, 255, 0)).save(buf, "PNG")
    png = buf.getvalue()

    connector = MagicMock()
    connector.id = "00000000-0000-0000-0000-000000000001"
    connector.name = "Acme Corp"
    connector.logo_url = None
    connector.logo_sha256 = None
    db = MagicMock()

    try:
        svc.save_connector_logo(
            db,
            connector=connector,
            file_bytes=png,
            filename="acme.png",
            content_type="image/png",
        )
    finally:
        set_storage(None)

    assert connector.logo_url == "https://rl-test.s3.us-east-1.amazonaws.com/connectors/acme-corp.png"
    assert connector.logo_sha256 and len(connector.logo_sha256) == 64
    # Object actually landed in the bucket under the slug-named key.
    assert ("rl-test", "connectors/acme-corp.png") in fake.objects
