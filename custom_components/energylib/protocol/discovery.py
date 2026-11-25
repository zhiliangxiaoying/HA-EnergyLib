"""Validate discovery metadata without treating advertisements as identity."""

import re
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Discovery:
    host: str
    port: int
    logger_sn: str
    logger_model: str
    logger_fw: str
    unit_id: int | None

    def metadata(self) -> dict[str, str]:
        """Advertised logger information, never business-verified inverter data."""
        return {
            "sn": self.logger_sn,
            "model": self.logger_model,
            "fw": self.logger_fw,
            "transport": "modbus_tcp",
            "api_ver": "1",
            "auth": "none",
            "source": "zeroconf",
        }


def parse_discovery(host: str, port: int, properties: Mapping[str, str]) -> Discovery:
    """Require the new logger contract; inverter identity comes from Modbus."""
    if (
        not isinstance(host, str)
        or not host.strip()
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise ValueError("Invalid discovery address")
    for key in ("sn", "model", "fw", "transport", "api_ver", "auth"):
        value = properties.get(key)
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or len(f"{key}={value}".encode("utf-8")) > 255
        ):
            raise ValueError("Invalid or missing discovery field")
    if properties["transport"] != "modbus_tcp":
        raise ValueError("Unsupported transport")
    # Local business-interface version, not the inverter register-map version.
    if properties["api_ver"] != "1":
        raise ValueError("Unsupported business interface")
    if properties["auth"] != "none":
        raise ValueError("Authenticated transports are not implemented")
    unit_id = None
    if "unit_id" in properties:
        value = properties["unit_id"]
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9]{1,3}", value) is None
            or not 1 <= int(value) <= 255
        ):
            raise ValueError("Invalid advertised unit ID")
        unit_id = int(value)
    return Discovery(
        host.strip(), port, properties["sn"], properties["model"], properties["fw"], unit_id
    )
