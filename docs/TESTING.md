# Testing / 测试说明

All results below come from **software simulators**. No physical inverter has been
tested yet; nothing here is evidence of hardware compatibility.

以下结果都来自**软件模拟器**，尚未在物理逆变器上测试，不能作为硬件兼容性的证据。

## Automated checks / 自动化检查

| Check | Result |
|---|---|
| Unit + loopback TCP tests (`python -m unittest discover`; dashboard tests run the strategy under Node) | 119 passed |
| Ruff lint / format | passed |
| Official **hassfest** container (`ghcr.io/home-assistant/hassfest`) | 1 integration, 0 invalid |
| HACS action (release-readiness workflow, v0.5.0) | passed |
| `python -m tools.ha_runtime_check` (HA 2026.8.2 isolated temporary config) | 14 checks passed |

## Home Assistant 2026.8.2 (HA Container) with simulators

Simulator: `tools/sinexcel_simulator.py` (V001B031 registers, writes enabled) and
`tools/mdns_peer.py` (discovery tests).

| Area | Scenario | Result |
|---|---|---|
| Discovery | Device advertises `_energylib._tcp.local.`; HA shows it under *Discovered*; user confirms; no entry is created silently | pass |
| Discovery | Same serial moves to a new port; HA updates the existing entry, entity IDs unchanged | pass |
| Discovery | Two devices at once; separate entries and entity IDs | pass |
| Discovery | Advertised `id` differs from the device serial; advertisement declares `auth=token` | both rejected, no flow |
| Data | 14 default measurements; power balance (load = PV + battery + grid) holds | pass |
| Data | Device offline → entities unavailable (never 0); back online → recovers automatically | pass |
| Signs | Device sends battery + = charging, grid + = feed-in; HA shows battery + = discharge, grid + = import | pass |
| Controls | Work mode, backup / minimum SOC, grid charging, export limit: write and read back | pass |
| Controls | Existing min > backup SOC conflict blocks only SOC writes | pass |
| Controls | Limits follow the device rated power (0x2008); values above it are rejected | pass |
| Remote | Finite remote charge (−1 kW / 5 s) ends automatically; heartbeat loss → device falls back | pass |
| TOU | Edit draft in options, save, read back; schedule status sensor | pass |
| Config | Form has only host / port / unit ID; no profile choice | pass |
| Config defaults (0.5.2) | Real HA manual form serializes port default 502; discovery and failed-submission port preservation covered by flow unit tests | pass |
| Unit ID input (0.5.3) | Real HA form serializes a number box defaulting to 1; native selector validation returns an integer for Modbus; fractional/out-of-range addresses rejected | pass |
| TOU validity (0.5.1) | Future/expired bounds preserved on save/clear; bounds included in read-back; inactive plan does not report charging | pass |
| Reconnect (0.5.1) | Rating drops 5 → 3 kW before the next poll; power/export/TOU requests of 4 kW rejected without writes | pass |
| Closure (0.5.1) | Close followed by queued writes or retained runtime calls cannot reconnect or start a new lease | pass |
| TOU status | Schedule sensor states show translated text (enum sensor) | pass |
| Dashboard | *Add dashboard → EnergyLIB* generates the page from the device; controls shown only when enabled; flow card used when installed | pass |
| Digital twin | Sidebar panel loads without setup; binds the EnergyLIB device's 5 values; numbers match the entities; car parked in the bay | pass |

## Not covered yet / 尚未覆盖

- Any physical inverter, firmware or communication dongle (实机、固件、通讯棒)
- HA starting after the device (HA 后启动) and DHCP IP changes (only port changes were tested)
- Cross-VLAN networks, IPv6
- Long-term energy statistics (7 days)
- Protocol assumptions listed in [FIRMWARE_QUESTIONS.md](FIRMWARE_QUESTIONS.md)
