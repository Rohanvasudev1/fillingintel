"""The ontology as text for prompts, and its hash (Step 7).

Generated from graph.ontology in the module's own order: labels, then edge
types. The text is never edited by hand; a run records SCHEMA_TEXT_SHA256 to
say exactly which ontology it used.
"""
from __future__ import annotations

import hashlib

from graph.ontology import EDGE_TYPES, LABELS, ONTOLOGY_VERSION, EdgeDef, LabelDef, Prop


def _properties(props: tuple[Prop, ...]) -> str:
    return ", ".join(
        f"{p.name}: {p.type}" + ("" if p.required else " (optional)") for p in props
    )


def _label_block(d: LabelDef) -> str:
    return "\n".join([
        f"{d.name} [{d.kind}]",
        f"  {d.description}",
        f"  key: {', '.join(d.keys)}",
        f"  properties: {_properties(d.properties)}",
    ])


def _edge_block(d: EdgeDef) -> str:
    pattern = f"({'|'.join(d.starts)})-[:{d.name}]->({'|'.join(d.ends)})"
    kind = f"{d.kind}, symmetric" if d.symmetric else str(d.kind)
    return "\n".join([
        f"{d.name} [{kind}]",
        f"  {d.description}",
        f"  {pattern}",
        f"  properties: {_properties(d.properties)}",
    ])


def schema_text() -> str:
    """The ontology as plain text, identical on every call for one version."""
    parts = [
        f"FilingIntel graph ontology, version {ONTOLOGY_VERSION}",
        "Node labels",
        *(_label_block(d) for d in LABELS),
        "Edge types",
        *(_edge_block(d) for d in EDGE_TYPES),
    ]
    return "\n\n".join(parts) + "\n"


SCHEMA_TEXT_SHA256 = hashlib.sha256(schema_text().encode("utf-8")).hexdigest()
