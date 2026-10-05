"""Every arm the harness can run, by name.  A new arm is one entry here."""
from __future__ import annotations

from types import MappingProxyType

from retrieve.arm import ArmSpec
from retrieve.vector import VECTOR

ARMS: MappingProxyType[str, ArmSpec] = MappingProxyType({VECTOR.name: VECTOR})
