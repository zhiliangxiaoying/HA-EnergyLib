"""Explicit allowlist; omit identities, addresses, raw registers and usage data."""


async def async_get_config_entry_diagnostics(hass, entry):
    c = entry.runtime_data
    r = c.device
    return {
        "profile_id": r.profile.id,
        "profile_version": r.profile.version,
        "protocol_version": r.identity.protocol_version,
        "arm_version": r.identity.arm_version,
        "machine_type": r.identity.machine_type,
        "capabilities": sorted(r.profile.capabilities),
        "last_update_success": c.last_update_success,
        "last_frame_count": r.last_frame_count,
        "read_retry_count": r.client.read_retries,
        "filtered_push_frames": r.client.filtered_push_frames,
        "write_retry_count": r.client.write_retries,
        "write_readback_recoveries": r.client.write_readback_recoveries,
        "power_direction_verified": r.profile.power_direction_verified,
        "controls_supported": r.profile.controls_supported,
        "controls_hw_verified": r.profile.controls_hw_verified,
        "rated_power_kw": r.profile.max_power_kw,
        "control_enabled": r.allow_control,
        "last_command": r.last_command,
        "last_control_timing": r.last_control_timing,
        "last_command_timing": c.last_command_timing,
        "available_points": sorted(k for k, v in (c.data or {}).items() if v is not None)
        if c.last_update_success
        else [],
    }
