"""Integration constants; these are client settings, not firmware guarantees."""

DOMAIN = "energylib"
CONF_UNIT_ID = "unit_id"
CONF_PROFILE = "profile"
PROFILE = "v001b031_single"
DEFAULT_UNIT_ID = 1
DEFAULT_PORT = 502
POLL_INTERVAL_SECONDS = 30
CONF_PROFILE_ID = "profile_id"
CONF_ALLOW_CONTROL = "allow_control"

# These optional native entities follow the user's remote-access opt-in only.
REMOTE_OPERATION_ENTITIES = {
    "remote_target_power": "number",
    "remote_duration": "number",
    "start_charge": "button",
    "start_discharge": "button",
    "stop_remote": "button",
    "remote_end_time": "sensor",
}

# One alarm-status entity owns the list/history and summary attributes.
RETIRED_ALARM_ENTITIES = {
    "active_alarms": "sensor",
    "fault_count": "sensor",
    "warning_count": "sensor",
    "any_fault": "binary_sensor",
    "any_warning": "binary_sensor",
}
# Former diagnostic entities now reported by the alarm status: BMS communication
# is in the alarm table (0x1110/0x1111), the grid bit 0x1014 is an alarm source.
RETIRED_DIAGNOSTIC_ENTITIES = {
    "grid_fault": "binary_sensor",
    "bms_comm_fault": "binary_sensor",
}
