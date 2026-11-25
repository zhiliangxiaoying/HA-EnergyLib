"""AECC local interface simulator for development and tests (no real device).

Answers like an AECC EMS with a Shenggerui inverter behind it (AECC's local
interface document, 2026-11-16):
- `{"Get": "EnergyParameter"}`: the system summary and device lists;
- `{"Get": "EnergycontrolparametersList"}`: control parameters;
- `{"Set": "DataTransmission"}` with a Modbus RTU frame for slave 1 (read 0x03,
  write 0x06 / 0x10 to the settings and on/off, with the device's range checks): the
  Shenggerui inverter's registers (protocol V2.7.1): battery, PV, AC OUT, grid
  CT, state, alarms, energy counters, machine code 54.
It optionally advertises itself the way AECC devices do: `_http._tcp` service
named `sxd-mdns-if-<sn>` with TXT `s_sn`, `s_ip`, `s_type`, `s_port`. Signs follow
the convention assumed in energylib_core/aecc_local.py (unverified): battery
output > 0 discharging, meter > 0 buying.

    python -m tools.aecc_local_simulator --bind 192.0.2.10 --port 15030 --mdns
"""

import argparse
import asyncio
import json
import math
import socket
import time

SERVICE = "_http._tcp.local."
SLAVE = 1
START = time.time()


def scenario(t=None):
    """A sunny day with a battery charging from solar and a little export."""
    t = time.time() if t is None else t
    wave = 0.5 + 0.5 * math.sin(t / 60)
    pv = round(2800 + 600 * wave)
    load = round(650 + 150 * wave)
    charge = round(1500 + 200 * wave)
    export = pv - load - charge
    return {
        "SSumInfoList": {
            "ControlEnableStatus": 1,
            "MeterTotalActivePower": -export,  # selling
            "TotalPVPower": pv,
            "TotalPVChargePower": charge,
            "TotalACChargePower": 0,
            "TotalSmartLoadElectricalPower": 0,
            "AverageBatteryAverageSOC": 64,
            "TotalBatteryOutputPower": 0,  # not discharging
            "TotalGridOutputPower": export,
            "TotalBackUpPower": 0,
            "TotalChargePower": charge,  # charging
        },
        "Storage_list": [],
        "PlugInfoList": [],
        "ChargerInfoList": [],
        "HotInfoList": [],
    }


def u32(value):
    value = int(value)
    return [value & 0xFFFF, value >> 16]  # low word first


def s16(value):
    return int(value) & 0xFFFF


# Writable settings (protocol V2.7.1 defaults, plus one charge period 01:00-05:00).
SETTINGS = {
    0xA02E: 2,  # charge source: mixed
    0xA09C: 5, 0xA09D: 100, 0xA09E: 10, 0xA09F: 100,  # stop, full, inverter->grid, grid->inverter SOC
    0xA0A0: 0, 0xA0A1: 0x0100, 0xA0A2: 0x0500, 0xA0A3: 0, 0xA0A4: 0, 0xA0A5: 0, 0xA0A6: 0,
    0xA0A7: 0, 0xA0A8: 0, 0xA0A9: 0, 0xA0AA: 0, 0xA0AB: 0, 0xA0AC: 0, 0xA0AD: 0,
}
STATE = {"on": True, "alarms": [0] * 8}
RANGES = {0xA02E: (0, 3), 0xA09C: (0, 100), 0xA09D: (0, 100), 0xA09E: (0, 100), 0xA09F: (10, 100),
          0xA0A0: (0, 1), 0xA0A7: (0, 1)}


def _valid(address, value):
    if address == 0x9C40:
        return True
    if address not in SETTINGS:
        return False
    if 0xA0A1 <= address <= 0xA0AD and address != 0xA0A7:
        return value >> 8 <= 23 and value & 0xFF <= 59  # 24:00 and 12:60 are refused
    low, high = RANGES.get(address, (0, 0xFFFF))
    return low <= value <= high


def write(address, values):
    """Apply a write like the inverter: Modbus exception code, or None."""
    for i, value in enumerate(values):
        if address + i != 0x9C40 and address + i not in SETTINGS:
            return 0x0B  # not writable (the device answers 0x0B, not 0x07)
        if not _valid(address + i, value):
            return 3
    for i, value in enumerate(values):
        if address + i == 0x9C40:
            if value in (0, 1):
                STATE["on"] = bool(value)
        else:
            SETTINGS[address + i] = value
    return None


def registers(t=None):
    """Shenggerui holding registers {address: value} (protocol V2.7.1) for the scenario.

    Directions as the protocol: battery current > 0 discharging, grid-side CT
    power > 0 buying.
    """
    t = time.time() if t is None else t
    s = scenario(t)["SSumInfoList"]
    hours = max(0.0, (t - START) / 3600)  # counters grow while the simulator runs
    pv = s["TotalPVPower"]
    load = pv - s["TotalChargePower"] + s["MeterTotalActivePower"]
    regs = {
        0x7530: 532,  # 53.2 V
        0x7531: s16(round(-s["TotalChargePower"] / 53.2 * 10)),  # charging: negative
        0x7532: 64,
        0x753B: 3800, 0x753C: round(pv / 2 / 380 * 10), 0x753D: pv // 2,
        0x753E: 3750, 0x753F: round(pv / 2 / 375 * 10), 0x7540: pv - pv // 2,
        0x754C: s16(load // 3), 0x7553: s16(load // 3), 0x755A: s16(load - 2 * (load // 3)),
        0x755E: s16(load),  # all loads on AC OUT
        0x756A: 2301, 0x756C: 5000,
        0x7584: s16(s["MeterTotalActivePower"]),  # one CT on phase A; selling: negative
        0x7594: 3 if STATE["on"] else 1, 0x7595: 2 if STATE["on"] else 0,  # inverter / standby
        0xC738: 54,
        **{0x75A5 + i: word for i, word in enumerate(STATE["alarms"])},
        **SETTINGS,
    }
    for address, kwh in ((0xCB52, 812.4 + 1.6 * hours), (0xCB54, 640.2), (0xCB56, 2310.7 + 3.1 * hours),
                         (0xCB58, 1520.3 + 0.7 * hours), (0xCB70, 95.4 + 0.3 * hours), (0xCB72, 230.8)):
        regs[address], regs[address + 1] = u32(kwh * 10)
    return regs


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def pass_through(command):
    """Answer one RTU frame (read 03, write 06 / 10) like the inverter would."""
    failed = {"FunctionCode": command.get("FunctionCode"), "ControlState": "fail"}
    try:
        frame = bytes.fromhex(str(command.get("TransmittedData", "")))
    except ValueError:
        return failed
    if len(frame) < 8 or crc16(frame[:-2]) != frame[-2:] or frame[0] != SLAVE:
        return failed  # nobody answers: the EMS reports a failed pass-through
    function, address = frame[1], int.from_bytes(frame[2:4], "big")
    if function == 3 and len(frame) == 8:
        count = int.from_bytes(frame[4:6], "big")
        regs = registers()
        data = b"".join(int(regs.get(address + i, 0)).to_bytes(2, "big") for i in range(count))
        body = bytes((SLAVE, 3, 2 * count)) + data
    elif function == 6 and len(frame) == 8:
        error = write(address, [int.from_bytes(frame[4:6], "big")])
        body = frame[:6] if error is None else bytes((SLAVE, 0x86, error))
    elif function == 0x10 and len(frame) == 9 + frame[6]:
        count = int.from_bytes(frame[4:6], "big")
        values = [int.from_bytes(frame[7 + 2 * i:9 + 2 * i], "big") for i in range(count)]
        error = write(address, values)
        body = frame[:6] if error is None else bytes((SLAVE, 0x90, error))
    else:
        return failed
    answer = body + crc16(body)
    return {"FunctionCode": function, "ControlState": "succeed",
            "TransmittedData": " ".join(f"{b:02X}" for b in answer)}


CONTROLS = {
    "EnergyManagement": "1",
    "ScheduledModeEnabled": "0",
    "MeterSelector": "1",
    "SystemMaxPowerLimit": "10800,3600,3600,3600",
    "PowerRegulationOperatingPoint": "10",
    "DeviceGridInputPowerLimit": "800",
    "PeakShavingConfig": "0:0",
    "BaseDischargePower": "0",
    "ValleyChargePowerStrategy": "0",
    "SystemAcChargePowerLimits": "4200,1400,1400,1400",
    "PeriodValidTimestamp": "2024-09-20 00:00:00",
    "PowerControlPeriod1": "1,09:00,23:59,1000,500,0,100,10",
}


async def serve_client(reader, writer, answer=scenario):
    try:
        while line := await reader.readline():
            try:
                request = json.loads(line)
            except ValueError:
                continue
            if request.get("Get") == "EnergyParameter":
                body = {"Response": "EnergyParameter", **answer()}
            elif request.get("Get") == "EnergycontrolparametersList":
                fields = request.get("RegControlField") or list(CONTROLS)
                body = {"Response": "EnergycontrolparametersList",
                        "ControlInfoField": {k: CONTROLS[k] for k in fields if k in CONTROLS}}
            elif request.get("Set") == "DataTransmission":
                body = {"Response": "DataTransmission",
                        "CommandResponse": pass_through(request.get("SetCommand") or {})}
            else:
                body = {"Result": "unsupported"}
            body["SerialNumber"] = request.get("SerialNumber")
            body["Target"] = "Web"
            writer.write(json.dumps(body).encode())  # AECC answers without a newline
            await writer.drain()
    finally:
        writer.close()


def service_info(sn, bind, port, device_type=131):
    from zeroconf import ServiceInfo

    return ServiceInfo(
        SERVICE,
        f"SXD-mDNS-IF-{sn}.{SERVICE}",
        addresses=[socket.inet_aton(bind)],
        port=port,
        properties={"s_sn": sn, "s_ip": bind, "s_type": str(device_type), "s_port": str(port)},
        server=f"sxd-{sn.lower()}.local.",
    )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15030)
    parser.add_argument("--serial", default="AECCSIM0001")
    parser.add_argument("--type", type=int, default=131)
    parser.add_argument("--mdns", action="store_true")
    args = parser.parse_args()
    server = await asyncio.start_server(serve_client, args.bind, args.port)
    zc = info = None
    if args.mdns:
        from zeroconf.asyncio import AsyncZeroconf

        zc = AsyncZeroconf(interfaces=[args.bind])
        info = service_info(args.serial, args.bind, args.port, args.type)
        await zc.async_register_service(info)
    print(f"AECC local simulator on {args.bind}:{args.port} (serial {args.serial})", flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        if zc:
            await zc.async_unregister_service(info)
            await zc.async_close()


if __name__ == "__main__":
    asyncio.run(main())
