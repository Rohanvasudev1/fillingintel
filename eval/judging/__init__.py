"""LLM judges for the harness (Step 5, ticket 07).

Importing this package sets ``RAGAS_DO_NOT_TRACK=true`` before any of its
modules imports ragas.  Ragas reads the variable once and caches the answer, and
only the exact string ``true`` turns its analytics off
(docs/research/ragas-deepeval-claude-judge.md, section 5).  The value is forced,
not defaulted, so an inherited ``false`` cannot turn tracking back on.
"""
import os

os.environ["RAGAS_DO_NOT_TRACK"] = "true"
