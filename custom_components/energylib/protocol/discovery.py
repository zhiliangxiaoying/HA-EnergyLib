"""Validate discovery metadata without treating advertisements as identity."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Discovery:
    host: str
    port: int
    advertised_id: str | None


def parse_discovery(host: str, port: int, properties: Mapping[str, str]) -> Discovery:
    """Only accept the supported transport; verify IDs later via Modbus."""
    if (
        not isinstance(host, str)
        or not host.strip()
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise ValueError("Invalid discovery address")
    transport = properties.get("transport")
    if transport is not None and transport != "modbus_tcp":
        raise ValueError("Unsupported transport")
    # api_ver=1 is our discovery contract, not a claim about ARM version numbers.
    if properties.get("api_ver", "1") != "1":
        raise ValueError("Unsupported discovery contract")
    if properties.get("auth", "none") != "none":
        raise ValueError("Authenticated transports are not implemented")
    identity = properties.get("id")
    if identity is not None and (
        not isinstance(identity, str) or not identity or identity != identity.strip()
    ):
        raise ValueError("Invalid advertised identity")
    return Discovery(host, port, identity)
