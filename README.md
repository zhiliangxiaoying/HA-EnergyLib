# EnergyLIB for Home Assistant

Local Home Assistant integration for **EnergyLIB hybrid inverters** that speak the
Sinexcel **V001B031** Modbus protocol through a communication dongle exposing
**Modbus TCP**. It reads power, energy and battery data for the Energy dashboard and
offers controls (work mode, SOC limits, export limit, remote charge/discharge and a
time-of-use schedule), allowed by default for newly added devices.

[中文说明](README.zh-Hans.md)

[User guide](docs/USER_GUIDE.md): installation and everyday steps with screenshots.
Pages and entity names follow Home Assistant's language (English and Simplified Chinese included).

> **Development preview.** Verified on one physical inverter (Sinexcel V001B031, three-phase
> 12 kW, through an EnergyLIB logger) and with software simulators. On that device, readings,
> alarms and the parameter controls (SOC limits, grid charging, export limit, EMS mode, TOU)
> were written and read back, and remote charge/discharge was tested on the device.
> Controls are on for newly added devices and can be turned off in the options. See [Testing](docs/TESTING.md) and the
> [open protocol questions](docs/FIRMWARE_QUESTIONS.md).

## Features

- Automatic discovery via mDNS (`_energylib._tcp.local.`) or manual setup
- Identity check by reading the inverter serial number; no duplicate devices on address changes
- 13 main measurements plus one alert status enabled by default (PV, battery, load and grid power; six cumulative
  energy counters; total SOC; running state; alert status; work mode)
- More diagnostic sensors available but disabled by default (grid voltage/frequency,
  battery voltage/current/SOH, PV strings, temperatures, faults)
- Controls (on by default for new devices, can be turned off): work mode, backup SOC, minimum SOC, grid charging,
  export limit, remote charge/discharge with a finite lease
- Time-of-use editor (up to 6 charge + 6 discharge periods) and services for automations
- Unavailable data is shown as *unavailable*, never as 0; diagnostics are redacted

Single inverter only; parallel systems are not supported.

## Requirements

- Home Assistant **2026.8.2** or newer
- An inverter dongle reachable over the LAN that serves Modbus TCP (port and unit ID as configured on the device)
- Multicast (mDNS) between HA and the device for automatic discovery; otherwise add it manually

## Installation

**HACS (custom repository)** — until the integration is listed in the default store:

1. HACS → ⋮ → *Custom repositories* → add this repository URL, type **Integration**.
2. Search for **EnergyLIB**, download it, and restart Home Assistant.

Some HACS versions show an “icon not available” placeholder in the repository list
even though EnergyLIB includes its brand icon. The list uses the older public brands
CDN; the bundled icon is available to Home Assistant after installation. See the
[HACS issue](https://github.com/hacs/integration/issues/5402).

**Manual:** copy `custom_components/energylib` into your `config/custom_components/`
directory and restart Home Assistant.

## Configuration

- **Discovered device:** *Settings → Devices & services* shows **EnergyLIB** under
  *Discovered*. Select **Add**, check the address and confirm.
  Discovery requires the new logger TXT contract (`sn`, `model`, `fw`, `transport`,
  `api_ver`, `auth`); optional `unit_id` pre-fills the address. Logger and inverter
  serials are separate; existing device/entity identities stay unchanged.
  Legacy `id`-only advertisements need a firmware update or manual setup.
  See [Discovery contract](docs/DISCOVERY.md).
- **Manual:** *Add integration → EnergyLIB*, then enter host, Modbus TCP port and
  unit ID (default 1). The manual form pre-fills port **502**; change it if the device
  uses another port. Discovery pre-fills the advertised port instead.

### Energy dashboard

Use the cumulative counters: *grid import energy*, *grid export energy*, *solar energy*,
*battery charge energy* and *battery discharge energy*. Check their meaning against the
inverter display before relying on them.

### EnergyLIB dashboard

*Settings → Dashboards → Add dashboard → EnergyLIB* creates a ready-made page from your
devices: energy flow, battery, today's and lifetime energy, a 7-day chart, status,
time-of-use schedule and activity. Control tiles appear only after you enable
**Allow control**. The page follows renamed entities and shows one tab per device.
The live energy-flow diagram is built in (no extra frontend card); a missing grid or
battery is marked with a red cross.
To use it in YAML: `strategy: {type: custom:energylib}`.

### Digital twin

The first tab is the everyday Overview. The following Digital twin tab shows a 3D home with live
solar, battery, grid and load flows from your EnergyLIB device (a selector appears with
several devices). The twin is bundled with
the integration and needs no setup. Its text follows the user's Home Assistant language.

### Sign convention

| Sensor | Positive | Negative |
|---|---|---|
| Grid power | importing (buying) | exporting (selling) |
| Battery power | discharging | charging |

The inverter reports the opposite signs (battery + = charging, grid + = feed-in); the
integration converts them.

## Controls

1. *Settings → Devices & services → EnergyLIB → Configure → Control access* → **Allow control**.
   Since 0.8.43 it is on for newly added devices; entries added earlier keep their setting.
2. Once controls are allowed and the device answers the required registers, new
   basic EMS/SOC/grid-charging/export controls are enabled by default. Existing
   user-disabled entities keep that preference. Battery dispatch still needs its
   separate remote permission; remote helpers remain disabled by default.

| Entity | Purpose |
|---|---|
| Work mode (select) | self-consumption, time of use, backup, PV charge priority, feed-in priority, peak shaving |
| Backup SOC / Minimum SOC (number) | battery reserve and discharge cut-off (minimum must not exceed backup) |
| Grid charging (switch) | allow charging the battery from the grid |
| Export limit (number) | maximum feed-in power, up to the inverter rating |
| Target power + Duration (draft numbers), Start charging/discharging and Stop buttons | Enter a positive kW magnitude and whole minutes (1–1440), then explicitly start/stop |
| Battery operation card | Choose direction, power and minutes, then Start; status/countdown/Stop in one card |

Draft edits do not write the inverter or change a running task. Drafts persist; tasks never resume after HA restart. See the [remote operation guide](docs/REMOTE_DISPATCH.md).

Remote control writes a heartbeat; if Home Assistant stops, the inverter falls back to
self-consumption after the timeout.

### Time-of-use schedule

*Configure → Time-of-use schedule*: add/edit/delete periods (type, start, end, power),
set *Weekdays and outside mode*, then **Preview and save** (optionally switching the work
mode to time of use). Limits: 6 charge + 6 discharge periods, one weekday set for the
whole table, no overlaps, no periods crossing midnight, no `24:00`.
Saving or clearing a plan preserves the device's effective-date bounds; the status
sensor respects that window. Effective dates are read-only in the current editor.

### Services

```yaml
action: energylib.set_tou_schedule
data:
  device_id: <EnergyLIB device>
  periods:
    - {type: charge, start: "01:00", end: "06:00", power: 3}
    - {type: discharge, start: "18:00", end: "22:00", power: 2.5}
  weekdays: [mon, tue, wed, thu, fri]
  outside_mode: self_consumption
  activate: true
```

- `energylib.set_battery_power` — `power_kw` (negative = charge) and `duration` in seconds
- `energylib.stop_battery_power` — stop remote dispatch immediately
- `energylib.clear_tou_schedule` — remove all periods

Each `set_tou_schedule` call replaces the whole table.

## Troubleshooting

- *Settings → Devices & services → EnergyLIB → ⋮ → Download diagnostics* gives the
  detected capabilities and the last command status without serial numbers or addresses.
- *Fault and warning status* shows named faults, warnings, indications and recent occurrences
  ([alarm guide and automation example](docs/ALARMS.md)). *Active alarms* keeps its count; `complete: false` means some
  alarm sources could not be read — it does not mean "no alarms".
- A discovered card for a device that no longer exists disappears after the mDNS cache
  expires; select **Ignore** meanwhile.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check . && python -m ruff format --check .
python -m unittest discover -v
```

A protocol simulator for local testing: `python -m tools.sinexcel_simulator --help`
(see [docs/OPERATIONS.md](docs/OPERATIONS.md) and [docs/SIMULATOR.md](docs/SIMULATOR.md)).
Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Disclaimer

Use at your own risk. Writing inverter settings can affect your battery, grid export and
backup supply; follow local regulations and the manufacturer's guidance. "Sinexcel" is a
trademark of its owner and is used only to describe protocol compatibility; brand images
belong to their respective owners.

## License

[Apache License 2.0](LICENSE) — Copyright 2026 zhiliangxiaoying. See [NOTICE](NOTICE).
The vendor protocol document is not included; the license does not cover brand images
or trademarks.

Remote charge/discharge has its own **Allow remote charge/discharge** switch, default off. Enabling only unlocks the combined overview card; Start executes the task. The native permission switch, parameter inputs and action buttons are disabled by default. See [Remote operation](docs/REMOTE_DISPATCH.md).

The work-mode UI is named **EMS mode**. Its selector is first in device Configuration and overview controls; entity IDs and mode values are unchanged.
