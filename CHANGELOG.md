# Changelog

All notable changes to this integration. Versions before the first public release
(`0.5.0.devN`) were internal previews.

## [Unreleased]

### Added
- EnergyLIB dashboard: *Add dashboard → EnergyLIB* generates the page from the user's
  devices (dashboard strategy `custom:energylib`, served by the integration). Controls
  appear only when enabled; the optional flow diagram uses Power Flow Card Plus when
  installed.
- EnergyLIB digital twin: a sidebar page ("EnergyLIB 数字孪生") with a 3D home showing
  live solar, battery, grid and load flows from the EnergyLIB entities. Bundled with
  the integration (three.js r128 and meshoptimizer, MIT; no CDN); models are original.

### Fixed
- Time-of-use schedule sensor states are translated (enum sensor with declared options).

## [0.5.3] — Unreleased

### Changed
- Show the Modbus unit ID as a numeric input box with its default value 1 visible.
  Preserve the selected address on retry; reject fractional addresses and pass
  validated addresses to the protocol as integers.

## [0.5.2] — Unreleased

### Fixed
- Default the manual connection form's Modbus TCP port to 502; preserve advertised
  ports during discovery and user-selected ports after a failed connection.

## [0.5.1] — Unreleased

### Fixed
- Preserve the device's TOU effective-date bounds when saving or clearing a plan;
  status respects the full stored validity range and read-back compares it.
- Re-probe capabilities after reconnect and validate power/SOC/mode limits inside
  the device lock before sending a control or schedule write.
- Reject queued commands and reads after runtime closure; unload cannot leave a
  new remote lease or reconnect through an old runtime reference.

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
