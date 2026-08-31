"""Т1 — content-addressed хранилище блобов."""

from __future__ import annotations

import hashlib

import pytest

from segregator.ingest.blobs import BlobRef, blob_relative_path, store_blob


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_store_blob_creates_file_with_sha256_layout(tmp_path):
    archive = tmp_path / "archive"
    source = _write(tmp_path / "src" / "faktura.pdf", b"%PDF-1.4 synthetic")

    ref = store_blob(archive, source)

    expected = hashlib.sha256(b"%PDF-1.4 synthetic").hexdigest()
    assert isinstance(ref, BlobRef)
    assert ref.sha256 == expected
    assert ref.was_new is True
    assert ref.path.exists()
    assert ref.path.read_bytes() == b"%PDF-1.4 synthetic"
    # Двухуровневый фанаут: blobs/ab/cd/<sha>.<ext>
    assert ref.path.parent.name == expected[2:4]
    assert ref.path.parent.parent.name == expected[:2]
    assert ref.path.suffix == ".pdf"


def test_store_blob_is_idempotent_for_identical_content(tmp_path):
    archive = tmp_path / "archive"
    a = _write(tmp_path / "src" / "a.pdf", b"same bytes")
    b = _write(tmp_path / "src" / "b.pdf", b"same bytes")

    first = store_blob(archive, a)
    second = store_blob(archive, b)

    assert first.sha256 == second.sha256
    assert first.path == second.path
    assert first.was_new is True
    assert second.was_new is False
    # Ровно один файл на диске — вторая копия не создаётся.
    stored = list((archive / "blobs").rglob("*.pdf"))
    assert len(stored) == 1


def test_store_blob_leaves_no_partial_file_behind(tmp_path):
    archive = tmp_path / "archive"
    source = _write(tmp_path / "src" / "x.pdf", b"content")

    store_blob(archive, source)

    # Прерванный прогон не должен оставлять .part — после успеха их точно нет.
    assert list((archive / "blobs").rglob("*.part")) == []


def test_store_blob_distinguishes_different_content(tmp_path):
    archive = tmp_path / "archive"
    a = _write(tmp_path / "src" / "a.pdf", b"alpha")
    b = _write(tmp_path / "src" / "b.pdf", b"beta")

    ref_a = store_blob(archive, a)
    ref_b = store_blob(archive, b)

    assert ref_a.sha256 != ref_b.sha256
    assert ref_a.path != ref_b.path
    assert len(list((archive / "blobs").rglob("*.pdf"))) == 2


def test_store_blob_rejects_missing_source(tmp_path):
    archive = tmp_path / "archive"
    with pytest.raises(FileNotFoundError):
        store_blob(archive, tmp_path / "nope.pdf")


def test_blob_relative_path_is_pure_and_stable():
    digest = "a" * 64
    rel = blob_relative_path(digest, ".pdf")
    assert rel.as_posix() == f"blobs/aa/aa/{digest}.pdf"
    # Чистая функция: тот же вход — тот же выход, без обращений к диску.
    assert blob_relative_path(digest, ".pdf") == rel


def test_store_blob_handles_extensionless_source(tmp_path):
    archive = tmp_path / "archive"
    source = _write(tmp_path / "src" / "noext", b"data")

    ref = store_blob(archive, source)

    assert ref.path.suffix == ""
    assert ref.path.name == ref.sha256
