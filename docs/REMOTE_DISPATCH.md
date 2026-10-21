# Remote charge/discharge operation / 远程充放电

## Everyday operation

In **EnergyLIB → Overview → Forced battery charge / discharge**:

1. Enable **Allow remote charge / discharge** (off by default), then choose **Charge** or **Discharge**.
2. Enter power (kW) and duration (whole minutes).
3. Press **Start charging/discharging**. The same card shows the result, actual
   power, remaining time and **Stop**.

An administrator enables **Allow control** once in integration options. With a
supported device, the overview inputs/status become available together.
Native operation entities start disabled. Since 0.8.9, enabling remote access also
restores the power, duration, three buttons, task-end timestamp and permission
switch, including previously user-disabled members of this specific group. Other
entities and device/config-entry disable reasons are not overridden. Read-only
installations still cannot dispatch.

Remote permission is separate from integration-wide **Allow control**. Old local
storage without this permission starts disabled. Enabling it saves local permission
and enables native entities; no device write, task start or mode change occurs.
HA normally reloads the entry after 30 seconds to instantiate restored entities.
The UI shows loading, and all start paths reject commands until those entities
are live. Do not work around this wait by calling the power service. Repeated
enabling when all entities are already live does not cause another reload.
Restoring disabled helpers during an active/unconfirmed task is rejected until
that task has stopped. Disabling
first stops an active/unconfirmed session with readback. A failed exit keeps access
enabled and reports the error. Both services and buttons enforce the permission;
Stop remains available for recovery while integration control access is allowed.
The permission persists through restart and restores linked entities; tasks never resume.
Disabling hides the six operation helpers after confirmed exit. The now-enabled
native permission switch remains visible and shows Off, allowing another opt-in.

Changing numbers only saves local HA settings, with no device write or reload.
It does not change a running task; press Start again to apply the new settings.
The UI uses the values captured by that click, not another client's later edits.
Invalid power/minutes and TOU conflicts are reported directly. If the work-mode
selector is enabled, **Exit time-of-use mode** is an explicit user action; Start
never changes the mode automatically.

The native device page starts with the helpers in the disabled-entities list and
restores them together when remote access is enabled. Entity IDs are unchanged.
The labels no longer include the implementation term "draft". The dashboard is
the everyday interface; native entities are also usable by automations.

The task target is negative for charging and positive for discharging. Actual power
is a separate measurement; device SOC limits, available solar and grid-charging
permission can constrain it. Starting a task does not automatically enable grid
charging or take over time-of-use mode. Exit TOU explicitly before remote dispatch.

The overview countdown is display-only. A device-level monotonic backend lease
ends the task even if no browser is open. At zero the UI waits for backend exit
confirmation. Write/readback/exit uncertainty is shown as **Execution unconfirmed**.
Task status changes are pushed after background expiry, without waiting for polling.

Since 0.8.13, the card labels the planned deadline **预计结束时间 / Estimated end time** (start + duration; cleared on an early stop); the optional entity is **远程充放电预计结束时间**.
An idle, stopped or completed task shows **暂无任务 / No active task**; an active
or unconfirmed task without a deadline shows **待确认 / Unconfirmed**, and missing
communication is not presented as idle. Active deadlines show date and time.
The independent `remote_end_time` timestamp is optional and disabled by default,
like the native operation inputs/buttons. It retains its unique ID and, since 0.8.9,
follows remote permission rather than a separate manual enable step. The combined card reads
the deadline from task-status attributes, without needing this timestamp entity.
The timestamp's native HA state remains unknown when empty; the friendly no-task
label belongs to the card, preserving the timestamp data type.

On an existing installation, an enabled remote permission restores this group on
load. Only these linked helpers follow remote opt-in; other entity preferences
remain unchanged. None of these display choices change the backend lease or
device watchdog. Since 0.8.10 all controls share the final desktop column:
EMS/SOC/grid/export settings, TOU and then forced charge/discharge at its bottom.
The two measurement columns contain energy values, charts and read-only activity.
Overview precedes Twin. On narrow screens HA stacks these blocks responsively,
keeping the control block last rather than placing manual dispatch between data.

Drafts survive reload/restart. Active tasks and their deadlines are never restored.
The integration attempts to stop an owned task during unload/shutdown; interruption
without a shutdown callback relies on the device watchdog. Heartbeats are internal.
The current profile uses heartbeat every 10 s, timeout 60 s, self-consumption escape;
these firmware assumptions still require supervised physical verification (Q3).
They are descriptions, not new writable settings.

The combined form uses `energylib.set_remote_parameters(device_id, power_kw,
duration_minutes)` to validate and save both local parameters together. Invalid
input leaves both previous values unchanged. This action never starts a task,
writes Modbus registers or reloads the entry. It requires integration control
access, and works even when all five native helpers are disabled.

The card controls permission through `energylib.set_remote_access(device_id,
enabled)`. It reads the confirmed local permission from task attributes rather
than depending on an enabled native switch. Integration-wide Allow control and
capability checks still apply, including when called by an automation.

## Compatibility

- `energylib.set_battery_power(device_id, power_kw, duration)` stays compatible:
  signed kW, duration in **seconds**, 1–86400; 0 kW remains accepted.
- `energylib.stop_battery_power(device_id)` uses the same path as Stop immediately.
- Since 0.8.1 the old `remote_power` number and `remote_control` switch are retired
  from the UI and their registry entries removed. Automations referencing those
  two old entity IDs must move to the public power/stop services. The public
  services, their units and 0 kW compatibility are unchanged.

```yaml
action: energylib.stop_battery_power
data:
  device_id: YOUR_DEVICE_ID
```

## 中文操作步骤

1. 管理员首次打开“允许控制”。
2. 进入 **EnergyLIB → 总览 → 电池强制充放电**，开启“允许远程充放电”（默认关闭），再选择方向。开启本身不执行任务。
3. 填功率和分钟数，点击开始；运行状态、实际功率和剩余时间在同一张卡片中。
4. 点击停止只结束本次手动任务；关闭远程权限时也会先确认停止，失败会提示。设备继续按自身工作模式运行。

目前仅通过软件设备验证，不以此声明物理逆变器控制兼容性。
