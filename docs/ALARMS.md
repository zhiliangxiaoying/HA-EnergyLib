# Faults, warnings and observation history

Since 0.8.5 the HA interface creates only one alarm summary, **Device alerts**
(`alarm_status`). The five duplicate entities `active_alarms`, `fault_count`,
`warning_count`, `any_fault` and `any_warning` are retired; their registry rows
are removed on setup, including user-renamed, enabled and disabled rows.

The same five values remain attributes of `alarm_status` for automations. Total
active points include faults, warnings, informational indications and unknowns.
`alarm_status` distinguishes normal, abnormal, unrecognized, incomplete and offline
and carries the current named list plus the last 100 occurrence records.
Counts are None when any source is missing; a previously confirmed active fault
stays present until its source reports recovery. Missing data never clears it.
Normal means a complete observation with no active alarm, rather than a generic
fallback. Polling, source dictionaries, observation history and change events
remain unchanged.

Automations using the five retired entity IDs must move to attributes such as
`state_attr('sensor.your_device_alerts', 'fault_count')` (substitute the actual
alert entity ID), or use `energylib_alarm_changed`. Do not convert a missing
count to zero. Other entity IDs, energy statistics and the local alarm store are
not migrated or recreated. Back up the component/config/registry before upgrading.

Since 0.8.12 the two diagnostic binary sensors `grid_fault` and `bms_comm_fault`
are retired as well and removed from the registry on setup. BMS communication
was already reported by the alarm table (`1110` BMS1, `1111` BMS2). The grid
summary bit FC02 Unit 1 `1014` is now an alarm source named 电网异常 / Grid
abnormal (severity warning, no evidence page cited until the trigger conditions
are confirmed). It is read only when the device answers it during setup, so an
unsupported address never leaves the alarm status incomplete. It costs one more
FC02 request per alarm poll; it cannot share the `1048` read because `1015`–`1047`
are status bits, not alarms.

Each detail includes `alarm_id`, FC, Unit/source, hexadecimal `code`, Chinese and
English name, severity, evidence page, first/last observation and `stale`.
The stable alarm ID is `profile:fcNN:uN:ADDR`; it contains no serial number.
Unknown mappings keep the address and severity `unknown`, without guessing.
Informational indications remain `info` and are not counted as faults/warnings.

## Protocol evidence

The local V001B031 PDF pages 46 and 48–61 were rendered and visually checked.
The 202 contiguous FC02 entries at 1048–1111 list machine types 1/2/3 as DEFAULT.
Page 46 identifies Unit 1 as DCAC and Unit 2 as DCDC. The profile retains its
existing two-source read range; actual extended-range support, source differences
and applicability to specific firmware still need hardware confirmation (Q11).
FC03 at the same numeric address is not an alarm mapping.
Names alone do not determine severity: 10A2 and 10AC are classified as warnings
by the table. The PDF is excluded from the repository. Public distribution rights
for protocol-derived mapping content must be checked before a public release.

## Persistence and events

Per-entry HA storage saves the last 100 occurrence records and the active set.
First observed, last valid observation and recovery observed are HA timestamps,
not inverter event timestamps. A new valid baseline after startup/reload updates
the records without emitting startup notifications. Each subsequent set change
emits `energylib_alarm_changed`, including replacement at an unchanged count.
Removal of the integration removes its local store. Ordinary reload preserves it.

The event fields are `device_id`, `alarm_id`, `change` (`raised`/`cleared`),
`source`, `code`, `name`, `severity`, `observed_at`. The integration sends no phone
notifications. Configure your own automation and notification target, for example:

```yaml
alias: EnergyLIB fault notification
triggers:
  - trigger: event
    event_type: energylib_alarm_changed
    event_data:
      device_id: YOUR_DEVICE_ID
      severity: fault
      change: raised
conditions: []
actions:
  - action: notify.mobile_app_YOUR_PHONE
    data:
      title: EnergyLIB
      message: >-
        {{ trigger.event.data.name }} (Unit {{ trigger.event.data.source }},
        0x{{ trigger.event.data.code }})
```

Filter by `alarm_id` for one specific source/point or by `change: cleared` for
recovery. Replace the placeholders before enabling; tests use local event records.

## 用户查看方式

EnergyLIB 总览只保留一张“设备告警”卡片。正常时显示“运行正常，无告警”，
有异常时直接显示名称和级别，来源及代码可展开，最近记录默认折叠。
五个重复数量／布尔实体已从设备页及已禁用列表中移除；对应值仍保留为“设备告警”的属性。
来源断线时保留上次有效记录并标注过期，不显示正常或伪造恢复。
中文／英文由 HA 的界面语言决定；设备页可查看告警状态及其属性。
历史最多保留最近 100 次发生，通信中断不产生新的恢复记录。
