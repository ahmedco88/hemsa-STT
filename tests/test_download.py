"""Model-download contract. The load-bearing property: a file is only ever given
its real name after its SHA256 matches, so a truncated or corrupt download can
never look like a working model."""

import pytest

from hemsa import config, download


class FakeResp:
    def __init__(self, url, history=()):
        self.url = url
        self.history = [type("H", (), {"url": u})() for u in history]


def sized(path, size: int) -> None:
    """A file of exactly `size` bytes without allocating it in RAM - the encoder
    alone is 652 MB, and these tests only ever look at st_size."""
    with path.open("wb") as fh:
        fh.truncate(size)


def test_manifest_is_internally_consistent():
    assert download.TOTAL_BYTES == sum(f.size for f in download.FILES)
    assert len(download.FILES) == 4
    for f in download.FILES:
        assert len(f.sha256) == 64 and f.sha256 == f.sha256.lower()
        assert download.file_url(f).startswith("https://huggingface.co/")


def test_needed_lists_everything_when_dir_is_empty(tmp_path):
    assert download.needed(tmp_path) == list(download.FILES)
    assert download.bytes_needed(tmp_path) == download.TOTAL_BYTES


def test_needed_treats_a_wrong_sized_file_as_missing(tmp_path):
    """The whole point: an aborted download leaves the right names at the wrong
    sizes, which used to read as 'model installed'."""
    for f in download.FILES:
        (tmp_path / f.name).write_bytes(b"x" * 10)
    assert download.needed(tmp_path) == list(download.FILES)


def test_needed_is_empty_when_sizes_match(tmp_path):
    for f in download.FILES:
        sized(tmp_path / f.name, f.size)
    assert download.needed(tmp_path) == []
    assert config.models_present({"models_dir": str(tmp_path)})


def test_models_present_is_false_for_a_truncated_model(tmp_path):
    for f in download.FILES:
        sized(tmp_path / f.name, f.size - 1)
    assert not config.models_present({"models_dir": str(tmp_path)})


def test_transport_accepts_huggingface_over_https():
    download._check_transport(FakeResp(
        "https://us.aws.cdn.hf.co/xet-bridge-us/abc",
        history=["https://huggingface.co/csukuangfj/model/resolve/main/x.onnx"]))


@pytest.mark.parametrize("final, history", [
    ("http://huggingface.co/x", []),                       # downgraded to http
    ("https://evil.example/x", []),                        # off-host
    ("https://huggingface.co.evil.example/x", []),         # suffix lookalike
    ("https://huggingface.co/x", ["http://huggingface.co/x"]),   # downgrade mid-chain
])
def test_transport_rejects_downgrades_and_foreign_hosts(final, history):
    with pytest.raises(download.InsecureRedirect):
        download._check_transport(FakeResp(final, history=history))


def test_run_is_a_no_op_when_everything_is_present(tmp_path):
    for f in download.FILES:
        sized(tmp_path / f.name, f.size)
    download.run(tmp_path)          # must not touch the network
