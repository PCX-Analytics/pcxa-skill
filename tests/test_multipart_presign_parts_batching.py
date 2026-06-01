"""Regression test: presign-parts must be batched at <=100 part numbers.

A second bug (surfaced uploading a 1.87GB mp4, issue #719 follow-on): the
backend's /files/multipart/presign-parts/ endpoint rejects any request with
more than 100 `part_numbers` ("Ensure this field has no more than 100
elements."). `_multipart_presign_and_put` asked for every missing part in a
single request, so any file needing >100 parts (≈ >1.6 GB at the default
16 MB part size) failed with HTTP 400. The fix batches the asks.
"""

import pcxa.commands.files as files


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class RecordingClient:
    """Fakes the multipart endpoints and records every presign-parts batch."""

    def __init__(self, *, initial_parts):
        self.presign_batch_sizes = []
        self.completed_parts = None
        self._initial_parts = initial_parts

    def _url(self, path):
        return f"https://api.example.com/{path}"

    def _request(self, method, url, json=None, **kwargs):
        if url.endswith("/multipart/initiate/"):
            return FakeResponse({
                "upload_id": "u1",
                "storage_key": "uploads/k",
                "part_urls": {str(n): f"https://put/{n}"
                              for n in range(1, self._initial_parts + 1)},
            })
        if url.endswith("/multipart/presign-parts/"):
            nums = json["part_numbers"]
            self.presign_batch_sizes.append(len(nums))
            assert len(nums) <= 100, f"presign-parts got {len(nums)} > 100"
            return FakeResponse({"part_urls": {str(n): f"https://put/{n}"
                                               for n in nums}})
        if url.endswith("/multipart/complete/"):
            self.completed_parts = json["parts"]
            return FakeResponse({})
        raise AssertionError(f"unexpected request: {url}")


def test_presign_parts_batched_for_large_file(tmp_path, monkeypatch):
    # 150 parts at a 1 KB part size → 150 KB file, no giant fixture needed.
    part_size = 1024
    total_parts = 150
    fp = tmp_path / "big.bin"
    fp.write_bytes(b"x" * (part_size * total_parts))

    put_calls = []

    def fake_put(url, data=None, headers=None, timeout=None):
        put_calls.append(url)
        return SimpleResp()

    class SimpleResp:
        headers = {"ETag": '"abc"'}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(files.requests, "put", fake_put)

    # Initiate hands back the first 10; 140 remain → batches of 100 + 40.
    client = RecordingClient(initial_parts=10)
    item = files._multipart_presign_and_put(
        client, fp, folder=465710, part_size=part_size, concurrency=4
    )

    assert client.presign_batch_sizes == [100, 40]
    assert len(put_calls) == total_parts            # every part uploaded
    assert len(client.completed_parts) == total_parts
    # All part numbers 1..150 present exactly once in the complete payload.
    nums = sorted(p["PartNumber"] for p in client.completed_parts)
    assert nums == list(range(1, total_parts + 1))
    assert item["storage_key"] == "uploads/k"
    assert item["file_size"] == part_size * total_parts
    assert item["folder"] == 465710


def test_no_presign_parts_call_when_initiate_covers_all(tmp_path, monkeypatch):
    part_size = 1024
    fp = tmp_path / "small.bin"
    fp.write_bytes(b"x" * (part_size * 5))

    class SimpleResp:
        headers = {"ETag": '"abc"'}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(files.requests, "put",
                        lambda *a, **k: SimpleResp())

    client = RecordingClient(initial_parts=5)  # initiate covers all 5 parts
    files._multipart_presign_and_put(
        client, fp, folder=None, part_size=part_size, concurrency=2
    )
    assert client.presign_batch_sizes == []
