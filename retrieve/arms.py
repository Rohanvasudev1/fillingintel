"""Every arm the harness can run, by name.  A new arm is one entry here."""
from __future__ import annotations

from types import MappingProxyType

from retrieve.arm import ArmSpec
from retrieve.graph_global import GRAPH_GLOBAL
from retrieve.graph_local import GRAPH_LOCAL
from retrieve.graph_traversal import GRAPH_TRAVERSAL
from retrieve.vector import VECTOR

ARMS: MappingProxyType[str, ArmSpec] = MappingProxyType(
    {spec.name: spec for spec in (VECTOR, GRAPH_LOCAL, GRAPH_TRAVERSAL, GRAPH_GLOBAL)})
