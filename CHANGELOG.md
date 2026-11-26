# Changelog

## 0.8.43.dev1

- Local-interface devices with an inverter behind the EMS, following the inverter's protocol V2.7.1.1: the EMS mode (self-consumption, time-of-use, sell first, backup first) as the mode selector and sensor (the inverter's supply is set to Hybrid first, where the mode takes effect); forced charge/discharge with a power and a duration the inverter times itself (the dispatch card and `energylib.set_battery_power`; a new task replaces a running one, and the mode cannot change while one runs); grid charging uses the grid-charging switch of these modes; a grid purchase limit (advanced, off by default). Saving the plan with activate switches to time-of-use. Five alarms added and three renamed after the protocol's table. Devices the EMS manages, read only: EV chargers as the EV charger power, state and connector energy of the EMS device (the overview's EV block), smart plugs and water heaters as their own devices (on, power, energy, voltage, current; on, power, temperature). The simulator has the new registers and `--accessories`.
- EV charger behind the inverter (from the protocol, not yet tested on a device): when the inverter reports a charger is enabled and its power answers, the integration adds its power and status (on by default), lifetime and session energy, session duration, meter link and phase voltages/currents (off by default; phases B/C only for a three-phase charger), and its 8 protections and 2 warnings join the device alarms. With control allowed: charger on/off (a main control, also for Apple Home) and the charging current limit (6–32 A), plus charge mode, green-power priority, plug and charge, battery charging and maximum grid top-up power (off by default). Writes are sent once and read back. The overview shows the charger's state and power with "now" and its settings after the inverter's controls, only for a device with a charger. The simulator gets `--charger`.
- Overview: a new house picture on the energy-flow card, modelled and rendered for this integration in a dark and a light version (1560 x 1208): a home with a garage, car and charger, living and dining rooms, roof panels, and the EnergyLIB inverter and battery. The cables, their animation and the readings work as before. NOTICE: the earlier partner artwork is gone; the car model is credited.
- Overview: the energy-flow card's two views (house picture and circles) are equally tall, so switching moves nothing on the page: the house spreads over the circles' height, with more room between the readings, the house and the state. Both drawings stand in the middle of the card, and the three readings are centred columns sized by the card's own width. In the circles the running state and mode stand one above the other, so they no longer cover the battery's name.
- Review fixes: an offline local-interface device is never offered as the inverter a newly discovered EnergyLIB logger replaces; a failed read of the inverter settings no longer leaves the previous time-of-use plan on the overview's card.
- Overview controls are Home Assistant's own tiles for every device: the mode card keeps only the row of modes and its line; backup and minimum SOC, export limit, grid charging and the export-limit switch are tiles with a slider or toggle below it, as on the local-interface page (user decision 2026-11-19). The card's own settings list (− / + and sliders) is removed.
- Local-interface devices: every device type is offered (the document's EMS is type 131, which the earlier type filter refused); answers are matched by `SerialNumber`. An inverter behind the EMS (type 131, answering at slave 1 through the RTU pass-through `DataTransmission`) is read entirely from its registers (read frames only): power from PV, battery voltage × current, grid-side CT and AC OUT plus CT load; battery level; running state; alarm count; lifetime solar, battery charge/discharge, consumption, grid import and export energy (so the Energy page and today card work); battery voltage/current, PV1–PV3, grid voltage and frequency. Live values every poll, counters once a minute. Other local-interface devices keep the JSON summary, whose battery power now subtracts `TotalChargePower`. Control parameters are read every 5 minutes into the diagnostics only. Still read-only. The overview of a source with counters but no controls uses three columns: now, today and totals, trends. The simulator answers pass-through reads and control parameters.
- Local-interface devices with an inverter behind the EMS: controls through the pass-through (never retried, always read back), off until "allow control" is switched on in the options: inverter on/off (confirmed from the startup state), backup SOC, minimum SOC, grid charging (mixed / PV only) and the time-of-use plan (3 charge + 3 discharge periods, no power or weekdays; the overview card hides those and allows 3 per kind). Alarms carry 80 names and levels. The EMS mode is pending a mode register; no export limit or remote charge/discharge on this model (no remote entities are created). The overview shows the control column for a source with controls but no EMS mode. The period-count message names the model's own limit.
- Overview: the inverter power switch leads the control column; the time-of-use card folds to its title line while the plan is not in use (a tap opens it; never folded with unsaved edits).
- Local-interface devices, read-only, over their local interface (preview, no device tested): discovered as `sxd-mdns-if…` on `_http._tcp` (inverter and storage types only), added after the user confirms and one read succeeds. Solar, battery, grid and derived home power, battery level and backup power as the standard entities, so the overview works unchanged. The power directions are an assumption until checked on a device; no energy counters, alarms or controls exist on this interface as far as is known. Its overview spreads the live cards over the three columns (house; power split and 24 h power; status and battery level), each under a heading, since it has no energy or control column. A simulator: `tools/aecc_local_simulator.py`.
- Phones: the overview's three columns are three pages ("Now", "Energy" — "统计" in Chinese, as "电量" reads as battery level — "Control") under one switch bar that stays at the top (`custom:energylib-tabs`), with the offline banner above it; the page shown last is remembered in that browser. Phones (under 768 px) and touch tablets (under 1366 px) get the pages, across the whole width (centred, at most 760 px); a browser with a mouse keeps the three columns at any width from 768 px (HA's screen visibility on the sections, with pointer type).
- The time-of-use plan is edited on the overview (`custom:energylib-tou`): one row per period, "+ Add period", the weekdays and the mode outside the periods; changes stay a draft until "Save to device" writes the whole plan once ("Undo changes" returns to the device's plan; a failed save keeps the draft) through `energylib.set_tou_schedule`, the same checked, read-back path as the integration's form, leaving the EMS mode as it is. The card has its own title, so the column has no separate "Time-of-use" heading above it. The schedule sensor is now on by default; where the integration had disabled it, it is enabled on setup (a user's own choice stays). Energy statistics: when Home Assistant's Energy page is set up with this inverter's energy counters, the energy column shows its own cards under today and the totals: the date selector (day / week / month / year with paging), usage by source, self-sufficiency and solar self-use gauges and the energy flow. Otherwise no history charts are shown (one way only: the Energy page's cards; the earlier hourly, daily and monthly charts are removed), and an admin gets a pointer to set the Energy page up. The settings are read when the page opens; unreadable settings count as not set up.
- Overview: the EMS mode and battery/grid settings are one card (`custom:energylib-mode`): a row of the modes the device offers, the current one marked, one line on what the picked mode does, and the settings as big numbers with − / + and a slider. A tap writes at once (a mode, a switch); a number is written once − / + or the slider rest for a second, so stepping 100 → 97 is one write. A failed write shows the device's values again with the reason. A mode set by another function (manual dispatch, dynamic tariff, voltage regulation) locks the choice. Modes are matched by the selector's own words, so another device profile shows its own modes (an unknown one with a plain icon, written by its word). The power switch keeps its own tile. The alarm card shows counts ("Warning 1 · Notice 4") with notices folded; "Device info" and "Edit schedule" are list rows instead of big buttons, device info under the alarms; the remote charge/discharge access looks like a switch. The three columns have one topic each: now (house, power split, the last 24 h of power and battery level, sign convention, running state and alarms), energy in kWh (today, lifetime totals, then Home Assistant's energy cards or our hourly, daily and monthly charts) and control (mode and settings, schedule, manual dispatch, then the log of what changed). The running-state chip sits on the card, so it also shows in the circle view; the circle view inside the house card no longer repeats "off grid".
- Overview: the six live tiles under the house are gone (the house already shows every reading); the running state is a chip at the bottom left of the house (green on grid, orange off grid; a tap opens its history), and the battery state is coloured (charging green, discharging orange). The today card names the current EMS mode at its top right, in the selector's words; a tap opens the mode selector. A device reporting "battery disconnected" hides the battery parts of the today card (live) and the battery-level chart (when the page opens). The chart is now titled "Battery level" in Chinese ("电池电量 · 24 小时"). Devices without all four power readings keep the tiles.
- The digital twin is removed: the EnergyLIB page now has only the overview (one tab per device), `/energylib_twin` is no longer served and the `custom:energylib-twin` view strategy is gone. Views that used it show an error until they are deleted. The 3D home page of the EnergyLIB Home integration takes its place. The Surface kiosk opens the EnergyLIB page (`/energylib-home`).
- Overview: the running-state and battery-level tiles moved to the left column, above the four power tiles; the log moved to the bottom of the right column. The middle column no longer repeats today's battery charge and discharge (they are in the today card).
- Offline banner at the top of the overview's left column, shown only while every power reading and the running state are unavailable: "last online" is the last real reading in the recorded history (two days back; statistics are not used because they carry values through hours HA was stopped).
- "Today" is one card instead of four statistics: the home's use with a source bar (grid import first, then battery discharge, solar the rest; marked as an estimate), battery charge/discharge and grid import/export side by side, and PV generation with the share used at home. Values are today's change in HA's statistics, refreshed every five minutes; tapping opens the counter.
- The overview no longer shows an "Add devices" link to the gateway's quick setup page: the inverter integration also runs on users' own Home Assistant and links to nothing outside itself. With the gateway, quick setup stays in the sidebar.
- House energy-flow card (`custom:energylib-house-flow`) on the overview, in place of the circle card: a picture of a home (light and dark versions in `frontend/house/`, 1170 x 906) with glowing pulses along the cables drawn in it — PV down to the inverter on the battery, grid entry through the wall box to the junction, inverter to the junction, junction into the home — each running in the direction power flows and faster with more power. Home, grid (importing/exporting) and PV readings sit above the roof; the battery state (charging/discharging/standby), charge level and power in front of it. Same readings and rules as the circle card (30 W noise, missing grid or battery never animated, negative load shown as 0 W). The circle card stays available as `custom:energylib-flow`. The pictures are part of the `?v=` version hash. A button at the top left switches between the house and the circle diagram; the choice is kept in that browser. Both views keep the card at the same size (the diagram's); the house sits centred in it. Without readings (inverter offline) both the house and the circle diagram still show, with "--" values, no flow and one line saying why.
- Automatic setup: when a verified logger is the only EnergyLIB logger announcing itself, no EnergyLIB entry exists (ignored ones included) and the user has never removed one, discovery adds it without asking, read-only. A Repairs issue ("Is this your inverter?") allows control once confirmed; discovery alone never enables control. After the user removes an inverter, or when several loggers announce together, discovery asks as before.
- Apple Home without hand-written entity IDs: the inverter power switch is now a main control instead of a configuration entity, and a new "EMS" selector offers the EMS modes as words in HA's system language (for example "自发自用", "Battery backup"); HomeKit names each option after its value, so Siri can switch modes by name. A HomeKit bridge that includes the `switch` and `select` domains picks both up for any inverter. New devices get only this selector, named "EMS mode"; devices upgraded from earlier versions keep the key-based selector, now named "EMS mode (legacy)", with its entity ID and enabled or disabled state, so existing automations keep working. The new `energylib.set_ems_mode` action switches the mode by its fixed key (for example `self_consumption`), independent of the language; the overview's "leave time-of-use" button uses it. After moving automations to it, the legacy selector can be disabled.
- The EnergyLIB dashboard appears in the sidebar by itself (a read-only strategy page at `/energylib`), unless the user already added a dashboard from the EnergyLIB strategy.
- English first: the overview cards, alarm, remote-task and dispatch cards and the digital twin read their text from shared translation files (`frontend/translations/en.json`, `zh-Hans.json`) and follow the user's Home Assistant language; other languages fall back to English. Adding a language needs one more file there plus the integration's `translations/`. The twin now defaults to English.
- Entity and device names follow a change of Home Assistant's system language without a restart (the entry reloads itself).
- English entity states read naturally (for example "Self-consumption", "On-grid", "Charge period" instead of lower-case codes).
- English user guide with screenshots (`docs/USER_GUIDE.md`).
- New "Inverter power" switch (on with control allowed, like the other controls): on and off are one-shot commands from the protocol, and the device's running-state bit confirms within 20 s; an inverter already in the target state gets no command. Offered only when the device answers that bit; switching off is refused during a remote charge/discharge task. The overview tile asks for confirmation before toggling. It can be bridged to HomeKit/Siri like any switch. Supervised hardware test (12 kW, on-grid): off and on were each confirmed within 3 s, the inverter was back on-grid about 30 s later, and settings were unchanged.
- Fix commands occasionally failing with "Unsupported control" (or "Control is not enabled") when they arrived during the 10-minute capability re-probe: the probe now runs on a copy and switches over at once, so commands and entities never see its half-built state, and a probe that fails keeps the previous configuration. Seen on hardware with a Siri mode change.
- Simulator: power on/off commands; when off, PV and battery are 0, the running state is standby and the home runs from the grid (no power off-grid).
- Built-in live energy-flow card replaces the optional third-party Power Flow Card Plus: values inside the circles, one line per node-to-node flow (PV serves the home first, then the battery, then export; the battery before the grid) with colour gradients and moving dots, glowing active circles, and the home ring split by power source. A missing grid (off-grid running state or the "grid disconnected" alarm) or battery ("battery 1 disconnected" alarm) is drawn grey with a red cross. The 30 W noise rule and the 0 W display of a negative load are unchanged.
- The alarm sensor exposes `grid_disconnected` / `battery_disconnected` (true/false, or null while alarm sources are incomplete).
- Simulator: `--scenario off_grid|no_battery|off_grid_no_battery` for these displays.
- Control is allowed by default for newly added devices (options `allow_control: true` at creation); it can still be turned off in the options. Entries added earlier keep their stored setting. Setup, discovery and control-permission texts say so.
- Peak shaving (work mode 8) is offered in the EMS mode selector only for rated power of 30 kW or more: the "Working Mode" note says it exists on 30/50 kW models only. On smaller models the selector still shows it when the device is already in it (set from the App), and a remote session or TOU save restores it as before. Seen on a 12 kW unit: in mode 8 the battery stayed idle, and the peak-shaving parameters answer exception 3.
- User guide: work-mode descriptions follow the working mode document.
- New diagnostic "Meter online" connectivity sensor, created only when the device answers that register and disabled by default. With the meter offline, grid power and export limiting cannot be trusted.

## 0.8.42.dev1

- Fix the EMS mode not being restored after a remote charge/discharge task on firmware that switches itself from manual (4) back to self-consumption (0) when manual control is disabled: for a session this integration owns, both cases now restore the mode used before the session (and the device's watchdog settings), skipping the write when the device is already in it. A mode set by another controller during the session is kept. Seen on hardware; stop and 60 s expiry verified.
- Overview: while the device is in a mode that cannot be selected (manual/remote, dynamic tariff, voltage regulation), the controls column shows the read-only EMS mode sensor instead of a native selector reading "unknown".

## 0.8.41.dev1

- Chinese work-mode names follow the mobile App: 自发自用模式, 定时充放电模式, 备电模式 (was 灾备) and 卖电优先模式. Mode codes, English names, entity IDs and automation values are unchanged.

## 0.8.40.dev1

- Fix the work mode being forced back to self-consumption about 60 s after a remote charge/discharge task ended: the device's EMS watchdog stays armed after manual control is off, and our heartbeat timeout (60 s) and escape mode (1, self-consumption) were left in place. Leaving remote control now restores the device's own watchdog settings recorded at the first start (a renewal keeps them); with unknown originals after a restart, our settings are neutralised with escape 0 ("keep running"). Found on hardware: feed-in priority set right after a stop was reverted at exactly last heartbeat + 60 s.
- The simulator's watchdog now stays armed after manual control is off, like the device.

## 0.8.39.dev1

- Fix remote charge/discharge never starting on real hardware: the inverter accepts the manual-control power only while manual control is enabled, so enable is now written first (the device answers exception 3 or ignores the power otherwise).
- Leaving remote control clears the power setpoint, disables manual control and restores the work mode used before the session: enabling switches the device to mode 4 (manual), which it keeps after the session ends. Unknown previous modes (after a restart) fall back to self-consumption, like the watchdog escape.
- A refused start that already entered manual mode is rolled back the same way.
- Hardware semantics (documented): discharge, including forced discharge, is only allowed above backup SOC plus the device's discharge hysteresis (10 %); with backup 52 % and SOC 53 % a 2 kW forced discharge did not run. Min SOC raises the discharge-ended alarm and stops discharge.
- Hardware semantics (documented): a charge setpoint is battery power (grid tops up PV); a discharge setpoint is the inverter's total output (PV + battery), so the battery supplies only the part PV does not cover, and surplus is exported.
- The simulator now follows the device: power writable only in manual mode, mode 4 on enable, heartbeat flag consumed at once.

## 0.8.38.dev1

- Verified on hardware with a battery connected: battery power/current signs (device + = charging, HA shows charging as negative), SOC, cell voltage and temperature, BMS current limits and SOH. BMS cabinet floats are engineering units (V, °C, A), as assumed.
- Capacity sensors (installed / remaining kWh) are created only when the BMS reports a capacity above 0; the first real BMS reports 0.
- Fix the live flow card drawing a charging battery as discharging: the card's default battery sign already matches EnergyLIB (positive = discharge), so `invert_state` is off again (found on hardware).
- The live flow card no longer animates a flow to the home from a few watts of meter mismatch (e.g. PV 1.58 kW, battery charging 1.57 kW, load 0 W): within 30 W, one supplying source in the card's input absorbs the difference. Sensors, tiles and statistics keep the raw values.
- Readings within 30 W (meter noise) no longer animate in the flow card or the twin: PV, battery and grid under 30 W count as 0 and a remaining imbalance within 30 W is absorbed, so e.g. 22 W of grid noise is not drawn as battery-to-grid.
- The digital twin applies the same 30 W rule as the flow card to its numbers, so both pages show the same PV, battery and grid values (before, 22 W of grid noise read 0 W in the card and 22 W in the twin).
- A negative home load no longer replaces the flow card or pauses the twin: it is drawn as 0 W; within 30 W (meter noise) without comment, beyond that with one line naming the raw reading. Sensors keep the device value.
- Device capabilities are re-probed every 10 minutes on the same connection (and on every reconnect); a change, such as a battery/BMS connected later, reloads the integration so entities are added or removed without a manual reload.

## 0.8.37.dev1

- Battery cabinet data from the BMS (created only when a BMS is reported, disabled by default): installed capacity and remaining energy (kWh), maximum/minimum cell voltage and temperature, BMS charge/discharge current limits. Values are read as float engineering units like the other points; scaling still needs a BMS-equipped device to confirm.
- Advanced battery limits: maximum SOC (50-100 %), maximum charge current and maximum discharge current (0-300 A), each offered only when the device answers it and written to battery 2 as well. Entities start disabled and appear in the overview controls once enabled. Maximum SOC below backup SOC is rejected from cached values.
- Fix the overview TOU card template raising UndefinedError in the HA log while the device is offline.

## 0.8.36.dev1

- Restore the minimum ≤ backup SOC check (battery 2 checked separately) using cached control values: a conflicting edit is rejected before any write, with no extra read. An unknown counterpart (empty cache) is left to the write read-back.
- An EMS mode change during a remote session is blocked from the cached state as before; only when the control cache is empty (start-up or recovery) one read of the remote-control state prevents overriding a session started from the App. Normal edits stay a single write plus its read-back.
- Point list: the export limit value no longer turns the limit switch on (separate since 0.8.33).

## 0.8.33.dev1

- When a basic parameter action is cancelled after a write starts, mark its result uncertain and invalidate cached control values until authoritative polling recovers them. Do not resend the cancelled write; preserve the normal two-request path for subsequent healthy independent parameter actions.

## 0.8.32.dev1

- Reject invalid basic control parameters without blocking the next command on a full telemetry refresh when no write started and the coordinator is healthy. Write/transport failures still recover through full read-back. Record the command key with HA timing so a previous basic action cannot be mistaken for a later TOU/remote operation.

## 0.8.31.dev1

- Make export power and its enable switch independent. Editing the power no longer also enables the limit; keep the existing switch state. On a healthy initialized connection this requires one write plus one strict read-back (two requests).

## 0.8.30.dev1

- Reuse each control write's exact verified raw read-back instead of rereading the target. SOC preflight reads only the opposite bound for each battery. Reuse the identity pinned to a healthy connection; reconnects still require identity/capability verification. Constraints, mode conflicts and failure recovery remain.
- Announce commands before the HA command lock and let background polls yield between every transport read, including profile-internal identity, alarms, controls, schedules and retry boundaries. Never interrupt an in-flight exchange or command-owned recovery; deferred polling cannot declare an offline device recovered. Diagnostics include HA command queue and service time.

## 0.8.29.dev1

- Reduce successful basic parameter actions to targeted dependency and read-back reads, retaining fresh identity verification, SOC constraints for both batteries, remote/EMS conflicts, exact write read-back and guarded retries. Unrelated control state remains on the regular polling tier; failed writes still use full recovery reads.
- Let pending basic controls end a telemetry poll between requests; unfinished measurements remain due rather than blocking the command behind a whole poll. Diagnostics separate queue time, operation time and request count without recording targets or identities.

## 0.8.28.dev1

- Remove the shared control getter's collision with HA NumberEntity.value and format native numeric read-backs to their configured step (export limit: 0.1 kW), avoiding float32 tails such as `6.800000190734863` in device and overview controls; protocol verification retains the raw value.
- Publish successful basic control read-back immediately instead of clearing every polling tier and waiting for another full telemetry cycle. Preserve the normal polling timer, refresh the read-only EMS state from verified controls, and retain full refresh on failures/recovery and TOU/dispatch actions.

## 0.8.27.dev1

- Add a standalone `custom:energylib-twin` view strategy using the same generated iframe and current asset hash as the EnergyLIB dashboard. Existing standalone views can keep their path without pinning an old iframe version in dashboard storage.

## 0.8.26.dev1

- Apply the overview's invalid-load protection to the digital twin: negative home load or missing power readings pause both SVG and 3D flow animations. Show the signed raw load and a metering warning, recover automatically when valid data returns, and preserve measurement/statistic values and control settings.

## 0.8.25.dev1

- Match Power Flow Card Plus's battery sign convention to the integration (+discharge), retain faint inactive paths and show small flows in watts. Pause the diagram on negative home load instead of inferring nonexistent battery/grid branches; retain the raw measurements and statistics, explain the condition, and resume when valid.
- Basic EMS/SOC/grid-charging/export controls are enabled by default once integration control access is explicitly allowed. Existing user-disabled entities retain their setting. Remote permission, input helpers and buttons remain disabled by default.
- The read-only EMS sensor exposes integration control permission, letting the remote card distinguish closed access from communication loss.

## 0.8.24.dev1

- Device firmware explicitly identifies the inverter and pads the ARM branch to two digits: `8.8` displays as `逆变器 V8.08` in Chinese or `Inverter V8.08` in English, using HA's system language. Raw version decoding, logger metadata, device/entity identifiers and statistics are unchanged.

## 0.8.23.dev1

- New 启用出口限制 switch turns the export limit off again; before, every write forced the limit on and entering 0 kW meant zero export, not "no limit". Any non-zero mode set from the App shows as on.
- Saving a TOU plan when the device's validity window has already expired starts a fresh window (today until 2099-12-31 23:59:59) instead of keeping the expired one, which made an activated plan never run. The schedule status shows 有效期已过 (validity expired).
- A remote start the device refuses (no session after read-back, e.g. without a battery) restores the device's heartbeat timeout and escape mode, returns to idle and reports that the device did not accept it, instead of staying unconfirmed.
- The heartbeat also accepts an Alive flag the device has already cleared, as long as the remote session is still active.
- Identity and control-state reads follow the 30 s tier; a 10 s energy-flow tick reads only the four powers on an already verified connection. Any reconnect is still identified before data is published.
- Polling tiers allow 1 s of slack: HA starts each poll exactly one interval after the previous start, so a 10 s point was skipped on every other tick.
- The TOU validity end is written as 23:59:59 of its day, as the real device stores it; a midnight end read back differently and made the save fail.

## 0.8.22.dev1

- A negative home load no longer shows as the home sending power out. Near zero output the real inverter reports load slightly below 0; the digital twin and the live flow card now show the home as 0 W (the twin notes the raw negative value), and the load sensor and statistics keep the device value.

## 0.8.21.dev1

- Energy-flow powers (光伏总功率, battery, load and grid power) update every 10 s instead of 30 s; the coordinator ticks every 10 s and other points keep their 30 s / 60 s / 5 min tiers. A fast tick costs the identity check plus four 2-register reads.

## 0.8.20.dev1

- Filter the EnergyLIB logger's known 28-byte push frames (validated length, version, CRC and end byte, at frame boundaries only) that a real logger sends on the Modbus connection; unknown frames still fail normal validation. Enabled explicitly by the EnergyLIB profile.
- Parameter writes listed as safe to repeat may retry up to 3 times after a lost acknowledgement: identity check, read-back and re-validation of limits, SOC constraints, battery 2, mode and session ownership come first; heartbeats, remote enable/disable and TOU blocks stay single-send.
- Diagnostics add filtered push frame, write retry and read-back recovery counters.
- Kiosk launcher waits until the dashboard route is ready before opening the browser.

## 0.8.19.dev1

- FC02/03/04 reads retry up to 3 times (0.2 / 0.4 / 0.8 s back-off) on communication errors, reconnecting and re-verifying the inverter identity before data is published. Modbus exception responses are not retried.

## 0.8.18.dev1

- Accept inverter serials padded with ASCII spaces (as on real hardware) as well as NULs; only trailing padding is removed.

## 0.8.17.dev1

- The digital twin paints the house, energy system, solar and garden first and loads the car afterwards; a car failure no longer blocks the scene.

## 0.8.16.dev1

- Align EnergyLIB FC02 wire requests to even starting addresses, based on physical-device read-only observations. Logical addresses, bit offsets, alarm IDs and entity IDs stay unchanged; odd quantities remain allowed. Requests stay within the profile block limit and address space. Generic Modbus transport and strict MBAP validation are unchanged.
- Add simulator scenarios for FC02 even-address-only firmware and reversed MBAP Length bytes, with TCP regression tests for running states, alarm sources, boundaries and errors.

## 0.8.15.dev1

- Identify each pending discovery card by its logger SN. The confirmation page shows the advertised logger SN, model and firmware in Chinese/English, including after a failed connection or parameter correction. Inverter identity remains separately verified through Modbus; existing device and entity IDs are unchanged.

## 0.8.14.dev1

- Battery 1 sensors are named 电池电量 / 电池电压 / 电池电流 again (English: Battery charge level / voltage / current); most systems have one battery. Battery 2 sensors keep the "电池 2" prefix and are still created only when battery 2 is enabled. Entity IDs are unchanged.
- Chinese PV power sensors are named 光伏总功率 (as in the protocol) / PV 1~4 功率 instead of 光伏功率 / 光伏 N 功率; energy totals and alarm texts keep 光伏. Entity IDs are unchanged.

## 0.8.13.dev1

- Name the battery 1 voltage/current sensors 电池 1 电压/电流 (Battery 1 voltage/current), matching 电池 1 电量. Entity IDs are unchanged.
- Add 电池 2 电压/电流, created only when battery 2 is enabled and disabled by default. Battery 2 current uses the same sign as battery 1 (Q2 follow-up).
- Rename the task deadline to 远程充放电预计结束时间 (Remote charge/discharge estimated end time); the card label is 预计结束时间. It is start time plus duration and is cleared on an early stop.

## 0.8.12.dev1

- Merge the two diagnostic entities into Device alerts: BMS communication was already in the alarm table (1110/1111); the grid summary bit becomes an alarm source named 电网异常 (warning), read only when the device supports it. The retired `grid_fault` and `bms_comm_fault` rows are removed on reload.
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

Tested with software simulators only. Controls follow the EnergyLIB protocol but are
**not verified on physical hardware**; see [docs/TESTING.md](docs/TESTING.md).

### Added
- Local integration for one inverter speaking the EnergyLIB Modbus protocol over Modbus TCP.
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
- Control limits follow the device rated power, capped by protocol ranges.
- Single device profile; the setup form asks only for host, port and unit ID.

### Requirements
- Home Assistant 2026.8.2 or newer.
