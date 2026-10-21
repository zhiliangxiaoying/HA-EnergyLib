"""New logger DNS-SD contract for standalone development peers only."""

import re
import socket

SERVICE = "_energylib._tcp.local."


def txt_properties(
    logger_sn,
    *,
    unit_id=1,
    model="TEST-LOGGER",
    fw="test-1.0",
    auth="none",
    transport="modbus_tcp",
    api_ver="1",
    omit=(),
    overrides=None,
):
    properties = {
        "sn": logger_sn,
        "model": model,
        "fw": fw,
        "transport": transport,
        "api_ver": api_ver,
        "auth": auth,
    }
    if unit_id is not None:
        properties["unit_id"] = str(unit_id)
    properties.update(overrides or {})
    return {key: value for key, value in properties.items() if key not in omit}


def service_info(logger_sn, bind, port, *, properties=None):
    from zeroconf import ServiceInfo

    try:
        address = socket.inet_aton(bind)
    except OSError as err:
        raise ValueError("mDNS needs a concrete LAN IPv4 address") from err
    if bind.startswith("127.") or bind == "0.0.0.0":
        raise ValueError("mDNS needs a concrete LAN IPv4 address")
    # The full identity stays in TXT even when the DNS labels are shortened.
    host = ("energylib-" + re.sub(r"[^a-z0-9-]", "-", logger_sn.lower()))[:63].rstrip("-")
    instance = (
        ("EnergyLIB-" + logger_sn.replace(".", "-").replace("\\", "-"))
        .encode("utf-8")[:63]
        .decode("utf-8", errors="ignore")
    )
    return ServiceInfo(
        SERVICE,
        f"{instance}.{SERVICE}",
        addresses=[address],
        port=port,
        properties=properties if properties is not None else txt_properties(logger_sn),
        server=f"{host}.local.",
    )
