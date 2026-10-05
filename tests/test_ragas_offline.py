"""Ragas imports and scores with the network blocked, and its analytics are off (ticket 07).

pytest-socket blocks the network only while a test runs, not while modules are
imported, and ragas pulls in LangChain, datasets and its own analytics module.
So the check runs in a fresh interpreter whose sockets fail on any use: it
imports the judging package, scores one answer with real Ragas metrics and a
scripted judge, and confirms Ragas's tracking switch reads as off.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_NO_NETWORK_SCRIPT = """
import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("network access attempted")

# Block every way out but keep the class, which ssl subclasses and asyncio's
# self-pipe (socketpair, no connect) needs.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

import os
assert os.environ["RAGAS_DO_NOT_TRACK"] == "false"  # inherited; the package must override it

from eval.judging.scoring import JudgeInput
from ragas._analytics import do_not_track
from tests.test_judge_scoring import ANSWERED, QUESTION, SOURCES, _judges, _replies
from tests.judge_fakes import ScriptedBackend

assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"
assert do_not_track() is True
scores = _judges(ScriptedBackend(_replies())).judge(
    JudgeInput(QUESTION, "lookup", ANSWERED, SOURCES), run=1)
assert scores.errors == {}, scores.errors
assert all(v is not None for v in scores.scores.values()), scores.scores
try:
    socket.create_connection(("pypi.org", 443))
except RuntimeError:
    print("ok")
"""


def test_ragas_imports_and_scores_with_the_network_blocked():
    env = {**os.environ, "TIKTOKEN_CACHE_DIR": str(REPO_ROOT / "vendor" / "tiktoken"),
           "RAGAS_DO_NOT_TRACK": "false"}  # the package must override an inherited value
    result = subprocess.run(
        [sys.executable, "-c", _NO_NETWORK_SCRIPT],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")
