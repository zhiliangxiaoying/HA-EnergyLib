# Changelog

## 0.8.14.dev1

- Battery 1 sensors are named 电池电量 / 电池电压 / 电池电流 again (English: Battery charge level / voltage / current); most systems have one battery. Battery 2 sensors keep the "电池 2" prefix and are still created only when battery 2 is enabled. Entity IDs are unchanged.
- Chinese PV power sensors are named 光伏总功率 (0x1270, as in the protocol) / PV 1~4 功率 instead of 光伏功率 / 光伏 N 功率; energy totals and alarm texts keep 光伏. Entity IDs are unchanged.

## 0.8.13.dev1

- Name the battery 1 voltage/current sensors 电池 1 电压/电流 (Battery 1 voltage/current), matching 电池 1 电量. Entity IDs are unchanged.
- Add 电池 2 电压/电流 (0x1216/0x1218, PDF p99), created only when battery 2 is enabled and disabled by default. Battery 2 current uses the same sign as battery 1 (Q2 follow-up).
- Rename the task deadline to 远程充放电预计结束时间 (Remote charge/discharge estimated end time); the card label is 预计结束时间. It is start time plus duration and is cleared on an early stop.

## 0.8.12.dev1

- Merge the two diagnostic entities into Device alerts: BMS communication was already in the alarm table (1110/1111); the grid summary bit 0x1014 becomes an alarm source named 电网异常 (warning), read only when the device supports it. The retired `grid_fault` and `bms_comm_fault` rows are removed on reload.
- Review fixes: alarm events use the non-deprecated device lookup; the alarm store is written only when occurrences change; one failed poll no longer leaves a running remote task unconfirmed; the alarm card keeps opened sections; the draft power maximum tolerates an unknown rating; `ha_runtime_check` waits for remote entities before the reconnect re-probe.

## 0.8.11.dev1

- Move lifetime energy to the left column directly above activity; put the seven-day energy chart in the middle column. Keep all controls in the final column.

## 0.8.10.dev1

- Keep every overview control in the final desktop column, with forced charge/discharge last. Move read-only charts/history into the two measurement columns so the manual card no longer wraps into a middle column.

## 0.8.9.dev1

- Put Overview before Digital twin and balance the everyday blocks on two- and three-column screens; long charts and forced charge/discharge follow the common measurements/settings.
- Link remote access to the six native operation entities. Enabling restores them and the permission switch; disabling confirms exit before hiding the six helpers. The switch stays available to show Off and permit re-enabling.
- Show explicit On/Off labels and a loading state. Block new tasks until HA finishes its native entity-enable reload; unchanged opt-in never reloads an active task.

## 0.8.8.dev1

- Keep task end time inside the forced charge/discharge card; show no active task, a full deadline, or unconfirmed status as appropriate.
- Default-disable the optional native task-end timestamp and keep it disabled on reload; preserve its ID and explicit user opt-ins.

## 0.8.7.dev1

- Rename the operation card to 电池强制充放电 / Forced battery charge / discharge; behavior is unchanged.

## 0.8.6.dev1

- Rename the read-only mode and writable selector to EMS模式 / EMS mode without changing their IDs or values.
- Prioritize the enabled EnergyLIB EMS selector in the native device Configuration card.
- Preserve native rendering, all other row order and explicit user enable states.

## 0.8.5.dev1

- Retire five duplicate alarm entities from the registry and entity factories, including disabled rows.
- Keep one alert-status entity with names, counts, completeness and persistent history as attributes.
- Preserve source-aware polling, stale-data handling, events and all other entity identities.

## 0.8.4.dev1

- Move the remote permission switch into the combined operation card too.
- All six native remote controls are optional and default disabled; the card works without them.
- Keep default-off permission, idle enable without writes, active-stop confirmation and durable settings.

## 0.8.3.dev1

- Keep power, minutes and task actions together in the overview; all five native helpers default disabled.
- Save the whole local form through one validated action; disabled native inputs are no longer required.
- Preserve parameter/entity identities, local persistence, default-off permission and explicit task start.

## 0.8.2.dev1

- Add a separate, persistent remote-operation permission, off by default for existing and new installs.
- Enabling access does not write the device or start a task; services and buttons enforce access.
- Disabling first confirms active remote exit; failure remains visible and does not claim access is off.
- Disable individual native action buttons by default; the overview combines access, settings and task actions.

## 0.8.1.dev1

- Consolidate charge/discharge, power, minutes, result and Stop into one everyday card.
- Remove draft wording, retire the old immediate-control entities, and keep public service compatibility.
- Prepare the new operation entities after control opt-in; preserve explicit user disables.
- Show one alert card; keep count/boolean helpers optional and default disabled.

## 0.8.0.dev1

- Add persistent local power/minute drafts, explicit charge/discharge/stop buttons and a public stop action.
- Publish task direction, target, actual power, UTC end time and backend expiry; the dashboard shows a countdown.
- Keep legacy IDs and immediate five-minute behavior, with explicit legacy names.
- No task auto-resume after restart; uncertainty remains visible and controls stay opt-in.

## 0.7.0.dev1

- Add source-aware bilingual fault/warning details, four summaries and an alarm status sensor.
- Retain stale sources; emit set-change events without startup duplicates; persist the last 100 occurrences.
- Add native dashboard alarm/history card and real HA regression checks.
- Simulator validation only; no physical inverter support claim.


All notable changes to this integration. Versions before the first public release
(`0.5.0.devN`) were internal previews.

## [0.6.1.dev1] — 2026-10-20

- Require the new logger DNS-SD contract: six mandatory TXT fields, optional
  `unit_id`; legacy `id` no longer supplies identity. Manual/existing entries work.
- Separate advertised logger metadata from the business-verified inverter SN;
  preserve inverter device/entity IDs across logger and endpoint changes.
- Deduplicate pending flows by logger and verified inverter; only update an endpoint
  after reading its inverter identity. Metadata-only updates do not reload, and
  connection changes reload once through the integration listener.
- Update standalone peers, fault fixtures and HA runtime discovery checks.
- Close peer connections before waiting for a simulator server to stop, avoiding
  shutdown hangs with persistent HA connections.
- Development preview; no physical logger or inverter verification claimed.

## [0.6.0] — 2026-10-20

Tested with software simulators only; controls are still **not verified on physical
hardware** (see [docs/TESTING.md](docs/TESTING.md)).

### Added
- EnergyLIB dashboard: *Settings → Dashboards → Add dashboard → EnergyLIB* builds the
  page from your devices (dashboard strategy `custom:energylib`, served by the
  integration). Tabs: **Digital twin** first, then **Overview** (energy flow, battery,
  today's and lifetime energy, 7-day chart, status, controls, time-of-use, activity).
  Controls appear only when enabled. The optional flow diagram uses Power Flow Card
  Plus when installed (HACS); it is not bundled.
- Digital twin: a 3D home with live solar, battery, grid and load flows from the
  EnergyLIB entities, bundled with the integration (three.js r128 and meshoptimizer,
  MIT; no CDN). The car is adapted from "Chevrolet Corvette (C7)" by Martin Trafas,
  CC BY 4.0, emblems removed; other models are original. See [NOTICE](NOTICE).

### Changed
- Modbus unit ID is a numeric input with its default 1 visible; the selected address is
  kept on retry, fractional addresses are rejected.
- The manual connection form defaults the Modbus TCP port to 502; advertised ports are
  kept during discovery and user-selected ports after a failed connection.

### Fixed
- Time-of-use schedule states are translated (enum sensor with declared options).
- TOU effective-date bounds are preserved when saving or clearing a plan; status
  respects the stored validity range and read-back compares it.
- Capabilities are re-probed after reconnect; power/SOC/mode limits are validated
  inside the device lock before a control or schedule write.
- Queued commands and reads are rejected after runtime closure; unloading cannot leave
  a new remote lease or reconnect through an old runtime reference.

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
