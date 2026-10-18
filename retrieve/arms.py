"""Every arm the harness can run, by name, and the router over them.  A new arm is one
entry in ``BASE_ARMS``."""
from __future__ import annotations

from types import MappingProxyType

from retrieve.arm import ArmSpec
from retrieve.graph_global import GRAPH_GLOBAL
from retrieve.graph_local import GRAPH_LOCAL
from retrieve.graph_traversal import GRAPH_TRAVERSAL
from retrieve.router import router_spec
from retrieve.vector import VECTOR

BASE_ARMS: MappingProxyType[str, ArmSpec] = MappingProxyType(
    {spec.name: spec for spec in (VECTOR, GRAPH_LOCAL, GRAPH_TRAVERSAL, GRAPH_GLOBAL)})
ROUTER = router_spec(BASE_ARMS)
ARMS: MappingProxyType[str, ArmSpec] = MappingProxyType({**BASE_ARMS, ROUTER.name: ROUTER})
