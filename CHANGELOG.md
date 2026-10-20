# Changelog

All notable changes to this integration. Versions before the first public release
(`0.5.0.devN`) were internal previews.

## [0.5.0] — 2026-10-20 (first public preview)

Tested with software simulators only. Controls follow the V001B031 protocol but are
**not verified on physical hardware**; see [docs/TESTING.md](docs/TESTING.md) and
[docs/FIRMWARE_QUESTIONS.md](docs/FIRMWARE_QUESTIONS.md).

### Added
- Local integration for one inverter speaking the V001B031 Modbus protocol over Modbus TCP.
- mDNS discovery (`_energylib._tcp.local.`) with serial-number identity check; address
  changes update the existing entry; advertised identity mismatches and unsupported
  authentication are rejected.
- 14 default measurements and capability-based optional diagnostics; unavailable data is
  never reported as 0.
- Opt-in controls (integration option + individually enabled entities), offered only when
  the device answers every control register: work mode, backup / minimum SOC, grid
  charging, export limit, remote charge/discharge with a finite lease and heartbeat.
- Time-of-use editor in the options flow (6 charge + 6 discharge periods) and the services
  `set_tou_schedule`, `clear_tou_schedule`, `set_battery_power`.
- Redacted diagnostics (capabilities, rated power, command status).
- Protocol simulator `tools/sinexcel_simulator.py` (optional mDNS) and discovery test peer
  `tools/mdns_peer.py`.

### Changed
- Power signs converted to the Home Assistant convention: grid + = import,
  battery + = discharge (the inverter reports the opposite).
- Control limits follow the device rated power (register 0x2008), capped by protocol ranges.
- Single device profile; the setup form asks only for host, port and unit ID.

### Requirements
- Home Assistant 2026.8.2 or newer.
