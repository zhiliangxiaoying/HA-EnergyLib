# Development instructions

## Scope

Build a Home Assistant integration for EnergyLIB devices. The project has an experimental read-only implementation. Current user-confirmed scope is a single inverter, standard Modbus TCP and `_energylib._tcp.local.` discovery. No parallel commands or controls in the initial version. Do not invent telemetry endpoints, register addresses, default ports, credentials, supported models or firmware capabilities. Read README.md and docs before making changes.

## Discovery

- Agreed service type: `_energylib._tcp.local.`.
- Reuse Home Assistant's shared zeroconf implementation; never start a separate mDNS instance in the integration.
- Verify discovery metadata through the device interface before trusting identity. The current plain Modbus TCP profile has no authentication; SN verification is not cryptographic authentication. Reject advertised authentication requirements that are not implemented; never enable controls based on discovery alone.
- Use a stable device ID; never use an IP address or user-editable name as unique identity.
- Deduplicate devices and refresh changed addresses without changing entity identity.
- Do not create a config entry silently from a discovery callback; ask for confirmation through HA's native config flow. Only exception (user decision, 2026-10-29): when a verified logger is the only EnergyLIB logger announcing itself, no EnergyLIB entry exists (ignored ones included) and the user has never removed an EnergyLIB entry, it may be added read-only (`allow_control: false`); controls stay off until the user confirms in Repairs.

## Device data and controls

- Keep transport, protocol decoding, model capabilities and HA entities separate.
- Share coordinated updates instead of polling once per entity.
- Document units, scaling, byte order, signedness and power-flow conventions.
- Do not report unavailable data as zero. Do not alter historical statistics to conceal a decoding issue.
- Serialize writes with reads where required by the device. Confirm writes through authoritative read-back before reporting success.
- Keep controls disabled until authorized protocol information and supervised hardware evidence are available.
- Never test live inverter, grid, battery or export controls without explicit authorization for the specific test.

## Validation

Add meaningful tests when executable code is introduced: discovery deduplication and address changes, protocol boundary values, invalid responses, unsupported capabilities, energy counter resets and control error/read-back handling.

Do not claim compatibility, HACS support or successful hardware testing without evidence. Record model, firmware, integration revision and verification scope without exposing credentials or private device identifiers.

## References and secrets

Reference repository: https://github.com/Pylontech-Research/HA-pylontech-force-H3X . Its device-specific assumptions are not EnergyLIB protocol facts. Respect licenses and preserve attribution if code is reused later. Do not publish vendor-restricted protocols, tokens, passwords, unredacted packet captures or HA backups.
