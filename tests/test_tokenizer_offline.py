"""The cl100k_base encoding loads from the repo, so tests and CI make no network calls.

tiktoken downloads its encoding file on first use, at import time, which
pytest-socket does not block (it only blocks during test runs).  The file is
vendored under vendor/tiktoken/ and conftest.py points TIKTOKEN_CACHE_DIR at it.
"""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "vendor" / "tiktoken"
# From tiktoken_ext/openai_public.py (tiktoken's own source for cl100k_base).
BLOB_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
EXPECTED_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"

_NO_NETWORK_SCRIPT = """
import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted")

socket.socket = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

from ingest.chunker import count_tokens
assert count_tokens("Revenue grew 65% in fiscal 2026.") > 0
print("ok")
"""


def test_tests_point_tiktoken_at_the_vendored_encoding():
    assert Path(os.environ["TIKTOKEN_CACHE_DIR"]) == VENDOR_DIR


def test_vendored_file_is_the_real_cl100k_base():
    cache_file = VENDOR_DIR / hashlib.sha1(BLOB_URL.encode()).hexdigest()
    assert hashlib.sha256(cache_file.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_chunker_tokenizes_with_the_network_blocked():
    env = {**os.environ, "TIKTOKEN_CACHE_DIR": str(VENDOR_DIR)}
    result = subprocess.run(
        [sys.executable, "-c", _NO_NETWORK_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "ok"
