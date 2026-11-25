// EnergyLIB dashboard strategy: builds the EnergyLIB page from the user's own devices.
// Served by the integration at /energylib_static/; users add it from
// Settings > Dashboards > Add dashboard > EnergyLIB, or with
//   strategy:
//     type: custom:energylib
// Entities are found by device and translation key, never by entity ID, so
// renamed entities keep working. Controls appear only when they are enabled.

// The integration loads this file with ?v=<hash of this file and its translations>;
// the translation files reuse it so browsers fetch new text after an update.
const VERSION = new URL(import.meta.url).searchParams.get("v") || "dev";

// Every visible string lives in translations/<language>.json. A missing key falls back to English; a language without a file
// shows English. Traditional Chinese (zh-Hant, zh-HK, zh-TW) has its own file;
// other Chinese uses Simplified. The 15 languages of the gateway (11-17).
const LANGUAGES = ["en", "zh-Hans", "zh-Hant", "de", "nl", "pl", "ja", "th", "fr", "es", "it", "ru", "vi", "ar", "ur"];
const TRANSLATIONS = Object.fromEntries(await Promise.all(LANGUAGES.map(async (lang) => {
  const url = new URL(`./translations/${lang}.json?v=${encodeURIComponent(VERSION)}`, import.meta.url);
  return [lang, (await import(url.href, { with: { type: "json" } })).default];
})));

function language(hass) {
  const lang = hass?.locale?.language || hass?.language || "en";
  if (TRANSLATIONS[lang]) return lang;
  if (lang.startsWith("zh")) return /hant|-tw|-hk|-mo/i.test(lang) ? "zh-Hant" : "zh-Hans";
  const base = lang.split("-")[0];
  return TRANSLATIONS[base] ? base : "en";
}

function translate(hass, section) {
  return { ...TRANSLATIONS.en[section], ...TRANSLATIONS[language(hass)][section] };
}

// Map "domain.translation_key" -> entity_id for one device.
function deviceEntities(hass, deviceId) {
  const found = {};
  for (const e of Object.values(hass.entities)) {
    if (e.platform !== "energylib" || e.device_id !== deviceId || !e.translation_key) continue;
    found[`${e.entity_id.split(".")[0]}.${e.translation_key}`] = e.entity_id;
  }
  return found;
}

function energyLibDevices(hass) {
  const ids = new Set(
    Object.values(hass.entities)
      .filter((e) => e.platform === "energylib" && e.device_id)
      .map((e) => e.device_id)
  );
  return [...ids]
    .map((id) => hass.devices[id])
    .filter(Boolean)
    .sort((a, b) => (a.name_by_user || a.name || "").localeCompare(b.name_by_user || b.name || ""));
}

// Pages only on phones and touch tablets (user decision 2026-11-19): a phone-width
// screen, or a touch screen narrower than 1366 px (three columns need about
// 1100 px beside HA's sidebar). A browser with a mouse keeps the columns at any
// width from 768 px, a narrow window included. The two lists are complements.
const NARROW = { condition: "screen", media_query: "(max-width: 767px), (max-width: 1365px) and (pointer: coarse)" };
const WIDE = { condition: "screen", media_query: "(min-width: 1366px), (min-width: 768px) and (pointer: fine)" };

// Whether HA's Energy page uses any of these statistics (its settings, or null).
function energyUses(prefs, ids) {
  const used = new Set((prefs?.energy_sources || []).flatMap((source) => [
    source.stat_energy_from, source.stat_energy_to,
    ...(source.flow_from || []).map((f) => f.stat_energy_from), ...(source.flow_to || []).map((f) => f.stat_energy_to),
  ]));
  return ids.some((id) => used.has(id));
}

function buildView(hass, device, t, multiple, energyPrefs = null) {
  const ent = deviceEntities(hass, device.id);
  const s = (key) => ent[`sensor.${key}`];
  const name = (id) => hass.entities[id]?.name || undefined;
  const has = (...ids) => ids.every(Boolean);
  const heading = (text, icon, style = "title") => ({ type: "heading", heading: text, icon, heading_style: style });
  const tile = (entity, icon, color, extra = {}) =>
    entity && { type: "tile", entity, name: name(entity), icon, color, vertical: false, features_position: "bottom", ...extra };
  const stat = (entity, label, icon) =>
    entity && {
      type: "statistic",
      entity,
      name: label,
      icon,
      period: { calendar: { period: "day" } },
      stat_type: "change",
      grid_options: { columns: 6 },
    };
  const compact = (cards) => cards.filter(Boolean);
  // A heading and the cards under it, or nothing when none of the cards exist
  // (a source without energy counters, alarms or controls).
  const titled = (head, cards) => (cards.some(Boolean) ? [head, ...compact(cards)] : []);
  // A device reporting "battery disconnected" has no battery to chart. The
  // strategy is built when the page opens; the today card follows live.
  const noBattery = hass.states?.[s("alarm_status")]?.attributes?.battery_disconnected === true;
  const power = ["solar_power", "load_power", "battery_power", "grid_power"].map(s);

  // Live flow: the built-in house card. It shows a notice while a power value is missing.
  const flowCards = [heading(t.flow, "mdi:transit-connection-variant")];
  if (has(...power)) {
    const [solar, load, battery, grid] = power;
    flowCards.push({
      type: "custom:energylib-house-flow",
      solar_entity: solar,
      load_entity: load,
      battery_entity: battery,
      grid_entity: grid,
      soc_entity: s("battery_total_soc"),
      running_entity: s("running_state"),
      // The EMS mode beside the running state (11-23: from the today card, now gone); a tap opens the selector.
      mode_entity: s("work_mode"), mode_select_entity: ent["select.home_mode"],
      alarm_entity: s("alarm_status"),
      text: {
        solar: t.solar, grid: t.grid, load: t.load, batteryShort: t.batteryShort,
        importing: t.importing, exporting: t.exporting, charging: t.charging, discharging: t.discharging,
        gridMissing: t.gridMissing, batteryMissing: t.batteryMissing, offGrid: t.offGrid,
        negativeLoad: t.negativeLoad, unavailable: t.flowMissing, standby: t.standby, aria: t.houseAria, toDiagram: t.toDiagram, toHouse: t.toHouse,
      },
    });
  }
  // The house shows every live reading, the running state and the battery; the
  // tiles are only for a device without all four power readings.
  if (!has(...power)) flowCards.push(
    ...compact([
      tile(s("running_state"), "mdi:power-plug", "green", { grid_options: { columns: 6 } }),
      tile(s("battery_total_soc"), "mdi:battery", "green", { name: t.soc, grid_options: { columns: 6 } }),
      tile(power[0], "mdi:solar-power", "amber", { grid_options: { columns: 6 } }),
      tile(power[1], "mdi:home-lightning-bolt", "blue", { grid_options: { columns: 6 } }),
      tile(power[2], "mdi:battery-charging", "green", { grid_options: { columns: 6 } }),
      tile(power[3], "mdi:transmission-tower", "purple", { grid_options: { columns: 6 } }),
    ])
  );

  // While every reading is unavailable, one banner on top says so.
  const banner = {
    type: "custom:energylib-offline",
    entities: compact([...power, s("running_state")]),
    text: { title: t.offlineTitle, since: t.offlineSince, long: t.offlineLong, help: t.offlineHelp },
  };
  // Today in one card: use, its rough sources, battery and grid in and out, PV.
  const today = s("load_energy") && {
    type: "custom:energylib-today",
    load_entity: s("load_energy"), solar_entity: s("solar_energy"),
    import_entity: s("grid_import_energy"), export_entity: s("grid_export_energy"),
    charge_entity: s("battery_charge_energy"), discharge_entity: s("battery_discharge_energy"),
    alarm_entity: s("alarm_status"),
    // The current mode (the sensor also names read-only modes); a tap opens the selector.
    mode_entity: s("work_mode"), mode_select_entity: ent["select.home_mode"],
    text: {
      todayUse: t.todayUse, sourceNote: t.sourceNote, charge: t.charge, discharge: t.discharge,
      importing: t.importing, exporting: t.exporting, todaySolar: t.todaySolar, selfUse: t.selfUse,
    },
  };

  // Today's battery charge and discharge are in the today card; without it
  // (no load counter) the two statistics stay at the top of the middle column.
  const batteryCards = today ? [] : titled(heading(t.battery, "mdi:home-battery"), [
    stat(s("battery_charge_energy"), t.todayCharge, "mdi:battery-arrow-up"),
    stat(s("battery_discharge_energy"), t.todayDischarge, "mdi:battery-arrow-down"),
  ]);

  // The controls are Home Assistant's own tiles (user decision 2026-11-19: one
  // design for every device). With the EMS selector the mode card replaces the
  // native selector tile; the settings stay tiles either way.
  const tileControls = (skip = []) => [
    // Power on/off first. It has no toggle feature: tapping the icon asks for confirmation first.
    ["switch.inverter_power", "mdi:power", "red", []],
    // The one EMS selector: options are words in HA's language (also for Apple Home).
    ["select.home_mode", "mdi:cog-transfer", "indigo", [{ type: "select-options" }]],
    ["number.backup_soc", "mdi:battery-lock", "teal", [{ type: "numeric-input", style: "slider" }]],
    ["number.min_soc", "mdi:battery-low", "orange", [{ type: "numeric-input", style: "slider" }]],
    ["number.export_limit", "mdi:transmission-tower-export", "blue", [{ type: "numeric-input", style: "slider" }]],
    ["switch.export_limit_enable", "mdi:transmission-tower-off", "indigo", [{ type: "toggle" }]],
    ["switch.grid_charge", "mdi:battery-charging-wireless", "purple", [{ type: "toggle" }]],
    // Advanced battery limits start disabled; they appear once enabled on the device page.
    ["number.max_soc", "mdi:battery-arrow-up", "cyan", [{ type: "numeric-input", style: "slider" }]],
    ["number.max_charge_current", "mdi:current-dc", "light-blue", [{ type: "numeric-input", style: "slider" }]],
    ["number.max_discharge_current", "mdi:current-dc", "deep-purple", [{ type: "numeric-input", style: "slider" }]],
  ].filter(([key]) => !skip.includes(key)).flatMap(([key, icon, color, features]) => {
    // Settings are half width, so two sit side by side on the phone pages as in a
    // section on the web (the mode selector stays full width).
    const card = tile(ent[key], icon, color, key === "switch.inverter_power"
      ? { icon_tap_action: { action: "toggle", confirmation: { text: t.powerConfirm } } }
      : { features, ...(key === "select.home_mode" ? {} : { grid_options: { columns: 6 } }) });
    if (!card) return [];
    if (key !== "select.home_mode" || !s("work_mode")) return [card];
    // Read-only modes are not selectable options. Display the authoritative
    // sensor while one is active instead of an "unknown" native selector.
    const readOnlyModes = ["dynamic_tariff", "remote", "voltage_regulation"];
    return [
      { type: "conditional", conditions: [{ condition: "state", entity: s("work_mode"), state_not: readOnlyModes }], card },
      { type: "conditional", conditions: [{ condition: "state", entity: s("work_mode"), state: readOnlyModes }], card: tile(s("work_mode"), icon, color) },
    ];
  });
  // The same order on every device: the power switch (it asks first), the mode
  // card (a tap writes), then the settings. Whether the inverter runs at all comes
  // before how it runs. A device with controls but no EMS mode yet (a local-interface device with a
  // register inverter) keeps the mode card's place, marked pending.
  const hasControls = ["switch.inverter_power", "number.min_soc", "number.backup_soc", "switch.grid_charge"].some((id) => ent[id]);
  const modeCard = ent["select.home_mode"] && s("work_mode")
    ? { type: "custom:energylib-mode", device_id: device.id, mode_entity: s("work_mode"), select_entity: ent["select.home_mode"] }
    : hasControls && !s("work_mode") && !ent["select.home_mode"] && { type: "custom:energylib-mode", device_id: device.id, pending: true };
  const controlCards = modeCard
    ? compact([tile(ent["switch.inverter_power"], "mdi:power", "red",
      { icon_tap_action: { action: "toggle", confirmation: { text: t.powerConfirm } }, grid_options: { columns: 12 } }),
      modeCard, ...tileControls(["switch.inverter_power", "select.home_mode"])])
    : tileControls();
  const link = (name, icon, path) => ({ name, icon, path });
  // Day / week / month / year history comes only from HA's Energy page (one way,
  // user decision 2026-11-19): when it is set up with this inverter's counters, its
  // own cards (date selector with paging, usage by source, self-sufficiency and
  // solar self-use, the energy flow). Otherwise none, and for an admin a pointer
  // to set the page up.
  const energyIds = compact(["solar_energy", "grid_import_energy", "grid_export_energy", "battery_charge_energy", "battery_discharge_energy"].map(s));
  const usesEnergy = energyUses(energyPrefs, energyIds);
  const energySetup = !usesEnergy && hass.user?.is_admin && energyIds.length > 0
    && { type: "custom:energylib-links", links: [link(t.energySetup, "mdi:chart-bar", "/config/energy")] };
  const dispatch = s("remote_status") && {
    type: "custom:energylib-dispatch", device_id: device.id,
    power_entity: ent["number.remote_target_power"], duration_entity: ent["number.remote_duration"],
    status_entity: s("remote_status"),
    control_status_entity: s("work_mode"),
    // State from the sensor (fixed keys); leaving TOU uses energylib.set_ems_mode.
    work_mode_entity: s("work_mode"),
    ems_entity: ent["select.home_mode"],
  };
  const statusCards = compact([
    heading(t.status, "mdi:information-outline"),
    s("alarm_status") && { type: "custom:energylib-alarms", entity: s("alarm_status") },
    { type: "custom:energylib-links", links: [link(t.deviceInfo, "mdi:devices", `/config/devices/device/${device.id}`)] },
  ]);
  const controlSection = compact([
    controlCards.some(Boolean) ? heading(t.controls, "mdi:tune") : tile(s("work_mode"), "mdi:cog-transfer", "indigo"),
    ...controlCards,
  ]);

  const dailyCards = titled(heading(t.today, "mdi:calendar-today"), [
    ...(today ? [today] : [
      stat(s("solar_energy"), t.todaySolar, "mdi:solar-power"),
      stat(s("grid_import_energy"), t.todayImport, "mdi:transmission-tower-import"),
      stat(s("grid_export_energy"), t.todayExport, "mdi:transmission-tower-export"),
    ]),
  ]);
  const historyCards = titled(heading(t.totals, "mdi:chart-box", "subtitle"), [
    ...[
      ["solar_energy", "mdi:solar-power", "amber"],
      ["load_energy", "mdi:home-lightning-bolt", "blue"],
      ["grid_import_energy", "mdi:transmission-tower-import", "purple"],
      ["grid_export_energy", "mdi:transmission-tower-export", "cyan"],
      ["battery_charge_energy", "mdi:battery-arrow-up", "green"],
      ["battery_discharge_energy", "mdi:battery-arrow-down", "teal"],
    ].map(([key, icon, color]) => tile(s(key), icon, color, { grid_options: { columns: 6 } })),
  ]);
  const sankey = has(...power) && {
      type: "custom:energylib-sankey",
      solar_entity: power[0], load_entity: power[1],
      battery_entity: power[2], grid_entity: power[3],
      running_entity: s("running_state"), alarm_entity: s("alarm_status"),
    };
  const powerChart = has(...power) && {
      type: "history-graph",
      title: t.powerTrend,
      hours_to_show: 24,
      grid_options: { columns: "full" },
      entities: [
        { entity: power[0], name: t.solar },
        { entity: power[1], name: t.load },
        { entity: power[2], name: t.batteryShort },
        { entity: power[3], name: t.grid },
      ],
    };
  const socChart = s("battery_total_soc") && !noBattery && {
      type: "history-graph",
      title: t.socTrend,
      hours_to_show: 24,
      grid_options: { columns: "full" },
      entities: [{ entity: s("battery_total_soc"), name: "SOC" }],
    };
  const signs = { type: "markdown", content: `<ha-icon icon="mdi:information-outline"></ha-icon> ${t.signs}` };

  const tou = s("tou_schedule");
  // With the schedule sensor (on by default since 0.8.43, needs controls on): the
  // plan is edited on the page. Without it: a link to the integration's form.
  // The card carries its own title, so no section heading above it.
  // The work-mode sensor marks a source that has controls (on or off); without it
  // (a read-only source) there is no schedule, no control note and no change log.
  // A source without an EMS mode but with controls (a local-interface device with a register
  // inverter, its mode still to be defined) counts too.
  const controllable = Boolean(s("work_mode") || hasControls);
  const touCards = !controllable ? [] : tou
    ? [{ type: "custom:energylib-tou", device_id: device.id, entity: tou,
      max_power_entities: compact([ent["number.remote_target_power"], ent["number.export_limit"]]) }]
    : [heading(t.tou, "mdi:calendar-clock"),
      { type: "custom:energylib-links", links: [link(t.touEdit, "mdi:calendar-edit", "/config/integrations/integration/energylib")] }];

  // Read-only history stays with the measurements, away from the control column.
  const logged = compact([s("running_state"), s("alarm_status"), s("work_mode"), ent["switch.grid_charge"]]);
  const logCards = logged.length ? [heading(t.log, "mdi:history"), { type: "logbook", hours_to_show: 24, entities: logged }] : [];

  const energyCards = usesEnergy
    ? [
      heading(t.energyHeading, "mdi:chart-areaspline"),
      { type: "energy-date-selection" },
      { type: "energy-usage-graph" },
      { type: "energy-self-sufficiency-gauge", grid_options: { columns: 6 } },
      { type: "energy-solar-consumed-gauge", grid_options: { columns: 6 } },
      { type: "energy-sankey" },
    ]
    : compact([energySetup]);

  // 1. now: the house, the power split, the last 24 h of power and battery level,
  //    the running state and alarms;
  // 2. energy, in kWh: today, the totals, HA's energy cards (or our charts);
  // 3. control: mode and settings, schedule, manual dispatch, and the log of what
  //    changed.
  const controlColumn = compact([...controlSection, ...touCards, dispatch || (!hasControls && controllable && { type: "markdown", content: t.controlsOff })]);
  // Today's curve (11-23, after a reference app's energy page): one reading at a
  // time, picked with live tiles, its kWh for the day and its power from midnight,
  // with a date switch. Under the house on the phone and on the web.
  const energyOf = (pairs) => pairs.filter(([key]) => s(key)).map(([key, label]) => ({ entity: s(key), label }));
  const curveTabs = [
    { key: "grid", label: t.grid, icon: "mdi:transmission-tower", power: power[3], net: true,
      energy: energyOf([["grid_import_energy", t.importing], ["grid_export_energy", t.exporting]]) },
    { key: "solar", label: t.solar, icon: "mdi:solar-power-variant", power: power[0], energy: energyOf([["solar_energy", t.dayGenerated]]),
      exportEnergy: s("grid_export_energy") },
    { key: "load", label: t.dayHome, icon: "mdi:home-lightning-bolt-outline", power: power[1], energy: energyOf([["load_energy", t.dayUsed]]) },
    { key: "battery", label: t.batteryShort, icon: "mdi:home-battery", power: noBattery ? null : power[2],
      energy: energyOf([["battery_discharge_energy", t.discharge], ["battery_charge_energy", t.charge]]) },
  ].filter((tab) => tab.power);
  const curve = curveTabs.length > 0 && {
    type: "custom:energylib-day-curve", alarm_entity: s("alarm_status"), tabs: curveTabs,
    text: {
      dayNet: t.dayNet, dayAria: t.dayAria, dayToday: t.dayToday, dayPrefix: t.dayPrefix, dayYesterday: t.dayYesterday, dayNoData: t.dayNoData,
      importing: t.importing, exporting: t.exporting, charging: t.charging, discharging: t.discharging,
      selfUse: t.selfUse,
    },
  };
  // One set of pages for the phone and the web (user decision 2026-11-23): the web
  // shows them side by side as columns, the phone one at a time under the bottom bar.
  // 1. now: the house, today's curve, today's energy and the totals, the battery
  //    level, the running state and alarms;
  // 2. statistics: the power split (Sankey), 24 h of power, HA's energy cards;
  // 3. control: mode and settings, schedule, manual dispatch, and the log of what
  //    changed. Without controls there is no third page and the log goes with "now".
  // On the web a heading sits above the house so the column tops line up; on the
  // phone the house is the top of the page, frameless and edge to edge.
  const house = flowCards.filter((card) => card.type !== "heading")
    .map((card) => (card.type === "custom:energylib-house-flow" ? { ...card, frameless: true } : card));
  // The live power split (Sankey) stays with "now", the running totals go to statistics (11-23,
  // user: the two were the wrong way round).
  // Today's curve carries today's kWh per reading; the today card repeated them (11-23, user:
  // removed; its self-use went to the curve's PV figures, its mode chip onto the house).
  const nowRest = compact([curve, ...(curve ? [] : dailyCards), sankey, socChart, ...statusCards,
    ...(controlColumn.length ? [] : logCards)]);
  const phoneNow = [...house, ...nowRest];
  // The note on the signs goes with the 24 h power chart it explains (11-23, user).
  const phoneEnergy = compact([...historyCards, powerChart, powerChart && signs, ...batteryCards, ...energyCards]);
  const controlPage = controlColumn.length ? [...controlColumn, ...logCards] : [];
  // On the web each column starts with a heading so the tops line up.
  const wide = [[...flowCards, ...nowRest], phoneEnergy.length ? [heading(t.tabEnergy, "mdi:chart-bar"), ...phoneEnergy] : [],
    controlPage].filter((cards) => cards.length);

  const deviceName = device.name_by_user || device.name;
  return {
    // No view icons: HA then shows the tab titles as text.
    // The brand in HA's top bar (user decision 2026-11-23), not a generic "Overview".
    title: multiple ? deviceName : "EnergyLIB",
    path: multiple ? device.id : "overview",
    type: "sections",
    max_columns: 3,
    sections: [
      // One topic per column on a wide screen; on a phone the same three as pages
      // under one switch bar (the last section), so the page is not one long scroll.
      ...wide.map((cards, i) => ({ type: "grid", visibility: [WIDE], cards: i === 0 ? compact([banner, ...cards]) : cards })),
      {
        type: "grid",
        visibility: [NARROW],
        // Across the whole width where HA has two columns (a tablet).
        column_span: 3,
        cards: [banner, {
          type: "custom:energylib-tabs",
          // A page with nothing on it (a read-only source has no control page) is left out.
          tabs: [
            // Without controls the log goes with the live page.
            { title: t.tabNow, icon: "mdi:flash", cards: phoneNow },
            { title: t.tabEnergy, icon: "mdi:chart-bar", cards: phoneEnergy },
            { title: t.tabControl, icon: "mdi:tune", cards: controlPage },
          ].filter((tab) => tab.cards.length),
        }],
      },
    ],
  };
}

class EnergyLibDashboardStrategy extends HTMLElement {
  static async generate(config, hass) {
    const t = translate(hass, "dashboard");
    const devices = energyLibDevices(hass);
    if (!devices.length) {
      return {
        title: "EnergyLIB",
        views: [{ title: "EnergyLIB", cards: [{ type: "markdown", content: t.noDevice }] }],
      };
    }
    // HA's Energy page settings: its cards are used where they cover this inverter.
    let energyPrefs = null;
    try {
      energyPrefs = hass.callWS ? await hass.callWS({ type: "energy/get_prefs" }) : null;
    } catch (e) { /* not set up, or not allowed: our own charts */ }
    return {
      title: "EnergyLIB",
      views: devices.map((device) => buildView(hass, device, t, devices.length > 1, energyPrefs)),
    };
  }
}

// Integration extra modules can run before the HA frontend installs its scoped
// custom-element registry; a definition made earlier is invisible to the
// frontend's lookup and the dashboard times out. Define once the app shell exists.
const STRATEGY_TAG = "ll-strategy-dashboard-energylib";
customElements.whenDefined("home-assistant").then(() => {
  if (!customElements.get(STRATEGY_TAG)) customElements.define(STRATEGY_TAG, EnergyLibDashboardStrategy);
  if (!customElements.get("energylib-alarms")) customElements.define("energylib-alarms", EnergyLibAlarmCard);
  if (!customElements.get("energylib-dispatch")) customElements.define("energylib-dispatch", EnergyLibDispatchCard);
  if (!customElements.get("energylib-mode")) customElements.define("energylib-mode", EnergyLibModeCard);
  if (!customElements.get("energylib-links")) customElements.define("energylib-links", EnergyLibLinksCard);
  if (!customElements.get("energylib-tou")) customElements.define("energylib-tou", EnergyLibTouCard);
  if (!customElements.get("energylib-tabs")) customElements.define("energylib-tabs", EnergyLibTabsCard);
  if (!customElements.get("energylib-remote-task")) customElements.define("energylib-remote-task", EnergyLibRemoteTaskCard);
  if (!customElements.get("energylib-flow")) customElements.define("energylib-flow", EnergyLibFlowCard);
  if (!customElements.get("energylib-house-flow")) customElements.define("energylib-house-flow", EnergyLibHouseFlowCard);
  if (!customElements.get("energylib-sankey")) customElements.define("energylib-sankey", EnergyLibSankeyCard);
  if (!customElements.get("energylib-offline")) customElements.define("energylib-offline", EnergyLibOfflineBanner);
  if (!customElements.get("energylib-today")) customElements.define("energylib-today", EnergyLibTodayCard);
  if (!customElements.get("energylib-day-curve")) customElements.define("energylib-day-curve", EnergyLibDayCurveCard);
});
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((s) => s.type === "energylib")) {
  window.customStrategies.push({
    type: "energylib",
    strategyType: "dashboard",
    name: "EnergyLIB",
    description: "Energy flow, battery, daily energy and controls for EnergyLIB inverters.",
    documentationURL: "https://github.com/zhiliangxiaoying/HA-EnergyLib",
  });
}

// Exposed for in-browser checks; not part of the HA strategy API.
window.energyLibDashboard = {
  buildView, deviceEntities, energyLibDevices, TRANSLATIONS, language, translate, flowModel, balanceWatts, flowSplit, sankeyModel, houseModel, todaySplit,
};

// Built-in live energy flow (no third-party card), laid out like HA's energy card:
// values inside the circles, one line per node-to-node flow with moving dots.
// Signs are the integration's: battery + = discharge, grid + = import.
// A card in a hidden section (the desktop columns on a phone, the phone pages on a
// wide screen) is still created and connected: periodic work checks this first.
function onScreen(el) {
  if (!el.isConnected) return false;
  return typeof el.checkVisibility === "function" ? el.checkVisibility() : el.offsetParent !== null;
}

const FLOW_TOLERANCE_W = 30;
const FLOW_COLORS = { solar: "#f7b731", grid: "#5299ff", battery: "#b28aff", home: "#38d39f" };

function stateWatts(hass, entity) {
  const state = entity && hass.states[entity];
  const value = Number(state?.state);
  if (!state || state.state === "" || !Number.isFinite(value)) return null;
  return value * ({ W: 1, kW: 1000, MW: 1e6 }[state.attributes?.unit_of_measurement] ?? 1000);
}

// A few watts of meter mismatch (PV 1.58 kW vs battery charge 1.57 kW with a 0 W
// load) must not draw a flow the device does not report. Readings under the
// tolerance count as 0; a remaining imbalance within it is absorbed by one source
// or sink. Sensors and statistics keep the raw values.
function balanceWatts(raw) {
  const w = { ...raw };
  for (const k of ["solar", "battery", "grid"]) if (Math.abs(w[k]) < FLOW_TOLERANCE_W) w[k] = 0;
  const excess = w.solar + w.battery + w.grid - w.load;
  if (excess !== 0 && Math.abs(excess) <= FLOW_TOLERANCE_W) {
    // Too much supply: trim a source (PV, grid import, battery discharge).
    // Too much demand: trim a sink (grid export, battery charge).
    const pick = excess > 0
      ? ["solar", "grid", "battery"].find((k) => w[k] >= excess)
      : ["grid", "battery"].find((k) => w[k] <= excess);
    if (pick) w[pick] -= excess;
  }
  return w;
}

// Everything the card draws, from HA states only (also used by the tests).
function flowModel(hass, config) {
  const raw = {
    solar: stateWatts(hass, config.solar_entity),
    battery: stateWatts(hass, config.battery_entity),
    grid: stateWatts(hass, config.grid_entity),
    load: stateWatts(hass, config.load_entity),
  };
  if (Object.values(raw).some((v) => v === null)) return { ready: false };
  const alarm = hass.states[config.alarm_entity]?.attributes || {};
  const offGrid = hass.states[config.running_entity]?.state === "off_grid";
  const missing = { grid: offGrid || alarm.grid_disconnected === true, battery: alarm.battery_disconnected === true };
  // A home cannot feed power out: a negative reading is drawn as 0 W. Beyond the
  // tolerance one quiet line under the card names the raw reading.
  const negativeLoad = raw.load < -FLOW_TOLERANCE_W ? raw.load : null;
  const w = balanceWatts({ ...raw, load: Math.max(raw.load, 0) });
  if (missing.grid) w.grid = 0;
  if (missing.battery) w.battery = 0;
  const dir = (v, inward) => (v > 0 ? inward : v < 0 ? (inward === "in" ? "out" : "in") : null);
  const soc = Number(hass.states[config.soc_entity]?.state);
  return {
    ready: true,
    watts: w,
    missing,
    offGrid,
    negativeLoad,
    soc: Number.isFinite(soc) ? soc : null,
    // "in" = towards the inverter, "out" = away from it.
    flows: { solar: dir(w.solar, "in"), grid: dir(w.grid, "in"), battery: dir(w.battery, "in"), home: dir(w.load, "out") },
  };
}

// Split the balanced powers into node-to-node flows (W): PV serves the home first,
// then charges the battery, then exports; the battery serves the home before the
// grid does. Each path is drawn only when it carries power.
function flowSplit(w) {
  const charge = Math.max(-w.battery, 0), discharge = Math.max(w.battery, 0);
  const imported = Math.max(w.grid, 0), exported = Math.max(-w.grid, 0);
  const solar = Math.max(w.solar, 0), load = Math.max(w.load, 0);
  const solarHome = Math.min(solar, load);
  const solarBattery = Math.min(solar - solarHome, charge);
  const solarGrid = Math.min(solar - solarHome - solarBattery, exported);
  const batteryHome = Math.min(discharge, load - solarHome);
  const gridHome = Math.min(imported, load - solarHome - batteryHome);
  const batteryGrid = Math.min(discharge - batteryHome, exported - solarGrid);
  const gridBattery = Math.min(imported - gridHome, charge - solarBattery);
  const keep = (v) => (v >= FLOW_TOLERANCE_W ? v : 0);
  return {
    solarHome: keep(solarHome), solarBattery: keep(solarBattery), solarGrid: keep(solarGrid),
    batteryHome: keep(batteryHome), gridHome: keep(gridHome),
    batteryGrid: keep(batteryGrid), gridBattery: keep(gridBattery),
  };
}

function formatWatts(w) {
  const a = Math.abs(w);
  return a < 1000 ? `${Math.round(a)} W` : `${(a / 1000).toFixed(2)} kW`;
}

// Keep the shared bus explicit: aggregate meters cannot identify which source
// supplies each sink. A labelled residual preserves mismatched measurements.
function sankeyModel(hass, config) {
  const model = flowModel(hass, config);
  if (!model.ready) return { ready: false, reason: "unavailable" };
  if (model.negativeLoad !== null || model.watts.solar < 0) {
    return { ready: false, reason: "invalid" };
  }
  const w = model.watts;
  const sources = [
    { key: "solar", color: FLOW_COLORS.solar, value: w.solar },
    { key: "discharging", color: FLOW_COLORS.battery, value: Math.max(w.battery, 0) },
    { key: "importing", color: FLOW_COLORS.grid, value: Math.max(w.grid, 0) },
  ].filter((node) => node.value > 0);
  const sinks = [
    { key: "load", color: FLOW_COLORS.home, value: w.load },
    { key: "charging", color: FLOW_COLORS.battery, value: Math.max(-w.battery, 0) },
    { key: "exporting", color: FLOW_COLORS.grid, value: Math.max(-w.grid, 0) },
  ].filter((node) => node.value > 0);
  const sum = (nodes) => nodes.reduce((total, node) => total + node.value, 0);
  const difference = sum(sources) - sum(sinks);
  if (difference !== 0) {
    (difference > 0 ? sinks : sources).push({ key: "difference", color: "#b58b53", value: Math.abs(difference) });
  }
  return { ready: true, sources, sinks, total: sum(sources), difference, missing: model.missing };
}

class EnergyLibSankeyCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{display:block;min-width:0} ha-card{display:block;box-sizing:border-box;padding:16px;width:100%} h2{font-size:20px;font-weight:400;margin:0 0 8px}
      svg{display:block;width:100%;max-width:100%;height:auto} text{fill:var(--primary-text-color);font-family:inherit;font-size:18px}
      .value{fill:var(--secondary-text-color);font-size:16px} .note,.notice{font-size:12px;line-height:1.6;color:var(--secondary-text-color);overflow-wrap:anywhere}
      .notice{padding:24px 0;font-size:14px} [hidden]{display:none!important}
    </style><ha-card><h2></h2><div class="notice" role="status"></div>
      <svg viewBox="0 0 560 330" role="img"></svg><div class="note"></div></ha-card>`;
  }

  set hass(hass) {
    if (!this.config) return;
    const t = translate(hass, "sankey");
    const root = this.shadowRoot;
    root.querySelector("h2").textContent = t.title;
    const svg = root.querySelector("svg");
    const notice = root.querySelector(".notice");
    const note = root.querySelector(".note");
    const model = sankeyModel(hass, this.config);
    const visible = model.ready && model.total > 0;
    // SVG does not implement the HTML hidden property consistently.
    svg.toggleAttribute("hidden", !visible);
    svg.replaceChildren();
    notice.hidden = visible;
    notice.textContent = !model.ready ? t[model.reason] : t.idle;
    note.hidden = !visible;
    if (!visible) return;
    note.textContent = [t.note, model.difference !== 0 ? t.imbalance : "",
      model.missing.grid ? t.gridMissing : "", model.missing.battery ? t.batteryMissing : ""].filter(Boolean).join(" ");
    const element = (tag, attributes, text) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const summary = [...model.sources, ...model.sinks]
      .map((node) => `${t[node.key]} ${formatWatts(node.value)}`).join(", ");
    svg.setAttribute("aria-label", `${t.title}. ${summary}`);
    svg.append(element("title", {}, `${t.title}. ${summary}`));
    const height = 200;
    const top = 65;
    // One linear scale for all ribbons. Nodes get spacing outside the bus.
    for (const [side, nodes] of [["source", model.sources], ["sink", model.sinks]]) {
      let busY = top;
      let nodeY = top - (nodes.length - 1) * 12;
      for (const item of nodes) {
        const size = item.value / model.total * height;
        const source = side === "source";
        const startX = source ? 166 : 284;
        const endX = source ? 276 : 394;
        const startY = source ? nodeY : busY;
        const endY = source ? busY : nodeY;
        const middleX = (startX + endX) / 2;
        const ribbon = element("path", {
          d: `M${startX} ${startY} C${middleX} ${startY} ${middleX} ${endY} ${endX} ${endY}`
            + ` V${endY + size} C${middleX} ${endY + size} ${middleX} ${startY + size} ${startX} ${startY + size} Z`,
          fill: item.color, "fill-opacity": "0.48",
        });
        ribbon.append(element("title", {}, `${t[item.key]} ${formatWatts(item.value)}`));
        svg.append(ribbon, element("rect", { x: source ? 158 : 394, y: nodeY, width: 8, height: size, fill: item.color }));
        const labelX = source ? 148 : 412;
        const align = source ? "end" : "start";
        const center = nodeY + size / 2;
        svg.append(element("text", { x: labelX, y: center - 3, "text-anchor": align }, t[item.key]),
          element("text", { x: labelX, y: center + 14, "text-anchor": align, class: "value" }, formatWatts(item.value)));
        busY += size;
        // Reserve label room even for tiny readings. The bus scale stays linear.
        nodeY += Math.max(size, 40) + 24;
      }
    }
    svg.append(element("rect", { x: 276, y: top, width: 8, height, fill: "var(--secondary-text-color)" }),
      element("text", { x: 280, y: 28, "text-anchor": "middle" }, t.bus));
    // Small branches can make the outside stacks taller than the shared bus.
    const extent = (nodes) => top - (nodes.length - 1) * 12
      + nodes.reduce((value, node) => value + Math.max(node.value / model.total * height, 40) + 24, 0);
    svg.setAttribute("viewBox", `0 0 560 ${Math.max(330, extent(model.sources), extent(model.sinks))}`);
  }

  getCardSize() { return 5; }
}

// 400 x 330 drawing, circles of radius 40, in the layout of HA's energy card.
const FLOW_NODES = ["solar", "grid", "home", "battery"];
const FLOW_CENTRES = { solar: [200, 62], grid: [62, 175], home: [338, 175], battery: [200, 288] };
// Path geometry runs from the first node to the second; "reverse" flows run back.
// a/b are the path's end points: each line fades from one node's colour to the other's.
const FLOW_PATHS = {
  solarGrid: { d: "M192,102 V130 Q192,170 152,170 H102", from: "solar", to: "grid", a: [192, 102], b: [102, 170] },
  solarHome: { d: "M208,102 V130 Q208,170 248,170 H298", from: "solar", to: "home", a: [208, 102], b: [298, 170] },
  solarBattery: { d: "M200,102 V248", from: "solar", to: "battery", a: [200, 102], b: [200, 248] },
  gridHome: { d: "M102,175 H298", from: "grid", to: "home", a: [102, 175], b: [298, 175] },
  batteryGrid: { d: "M192,248 V220 Q192,180 152,180 H102", from: "battery", to: "grid", a: [192, 248], b: [102, 180] },
  batteryHome: { d: "M208,248 V220 Q208,180 248,180 H298", from: "battery", to: "home", a: [208, 248], b: [298, 180] },
};
// Where a missing grid/battery gets the red cross on its main line.
const FLOW_CROSS = { grid: [150, 175], battery: [200, 224] };
const FLOW_ICONS = {
  solar: "mdi:solar-power-variant",
  grid: "mdi:transmission-tower",
  home: "mdi:home",
  battery: "mdi:battery-high",
};

class EnergyLibFlowCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.text = config.text || {};
    this.classList.toggle("embedded", config.embedded === true);
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    const pos = ([x, y]) => `left:${x / 4}%;top:${(y / 330) * 100}%`;
    const gradient = ([id, p]) => `<linearGradient id="grad-${id}" gradientUnits="userSpaceOnUse"`
      + ` x1="${p.a[0]}" y1="${p.a[1]}" x2="${p.b[0]}" y2="${p.b[1]}">`
      + `<stop offset="0" stop-color="${FLOW_COLORS[p.from]}"/><stop offset="1" stop-color="${FLOW_COLORS[p.to]}"/>`
      + `</linearGradient>`;
    const path = ([id, p]) => `<path class="base" id="base-${id}" d="${p.d}"/>`
      + `<path class="line" id="line-${id}" d="${p.d}" stroke="url(#grad-${id})"/>`
      + `<circle class="dot" id="dot-${id}" r="4" fill="${FLOW_COLORS[p.from]}">`
      + `<animateMotion id="move-${id}" dur="2s" repeatCount="indefinite" calcMode="linear"`
      + ` keyPoints="0;1" keyTimes="0;1"><mpath href="#base-${id}"/></animateMotion></circle>`;
    const cross = ([id, [x, y]]) => `<g class="cross" id="cross-${id}" transform="translate(${x} ${y})">`
      + `<circle r="9"/><path d="M-3.5 -3.5 L3.5 3.5 M3.5 -3.5 L-3.5 3.5"/></g>`;
    const node = (id) => `<div class="node ${id}" id="node-${id}" style="${pos(FLOW_CENTRES[id])};--c:${FLOW_COLORS[id]}">`
      + `<div class="label" id="label-${id}"></div>`
      + `<div class="ring" id="ring-${id}"><div class="inner">${id === "battery"
        // Battery: an outline that fills with the charge level, the percentage inside.
        ? `<div class="batt" id="batt"><div class="fill" id="fill"></div><span id="soc"></span></div>`
        : `<ha-icon icon="${FLOW_ICONS[id]}"></ha-icon>`}`
      + `<div class="value" id="value-${id}"></div></div>`
      + `<div class="badge">✕</div></div></div>`;
    root.innerHTML = `<style>
      :host{display:block}
      ha-card{padding:16px 16px 8px}
      .head{display:flex;align-items:center;justify-content:space-between;gap:8px}
      .title{font-size:20px;line-height:1.3}
      .chip{font-size:12px;padding:2px 10px;border-radius:12px;background:rgba(229,57,53,.15);color:#e53935}
      .stage{position:relative;width:100%;max-width:420px;aspect-ratio:400/330;margin:28px auto 30px}
      svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
      .base{fill:none;stroke:var(--divider-color,#555);stroke-width:1.5;opacity:.45}
      .base.missing{stroke-dasharray:4 5;opacity:.6}
      .line{fill:none;stroke-width:2.5;stroke-linecap:round;display:none}
      .line.on{display:inline}
      .dot{display:none}
      .dot.on{display:inline}
      .cross{display:none}
      .cross.on{display:inline}
      .cross circle{fill:#e53935}
      .cross path{stroke:#fff;stroke-width:2;stroke-linecap:round}
      .node{position:absolute;transform:translate(-50%,-50%);width:80px;height:80px;cursor:pointer}
      .label{position:absolute;left:50%;transform:translateX(-50%);top:84px;font-size:12px;white-space:nowrap;color:var(--secondary-text-color)}
      .node.solar .label{top:auto;bottom:84px}
      .ring{position:relative;width:80px;height:80px;border-radius:50%;box-sizing:border-box;border:3px solid var(--c);background:var(--card-background-color,#1c1c1c)}
      .ring.mix{border:none;padding:3px;background:var(--mix)}
      .node.active .ring{animation:glow 2.8s ease-in-out infinite}
      @keyframes glow{
        0%,100%{box-shadow:0 0 0 5px color-mix(in srgb,var(--c) 14%,transparent),0 0 18px 2px color-mix(in srgb,var(--c) 40%,transparent)}
        50%{box-shadow:0 0 0 8px color-mix(in srgb,var(--c) 10%,transparent),0 0 28px 6px color-mix(in srgb,var(--c) 55%,transparent)}
      }
      .inner{width:100%;height:100%;border-radius:50%;background:var(--card-background-color,#1c1c1c);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px}
      .inner ha-icon{--mdc-icon-size:24px;color:var(--c)}
      .value{font-size:12px;font-weight:400;line-height:1.2;white-space:nowrap;color:color-mix(in srgb,var(--c) 80%,var(--primary-text-color,#fff))}
      .batt{position:relative;width:38px;height:18px;margin:0 3px 3px 0;box-sizing:border-box;border:1.5px solid var(--c);border-radius:4px;display:flex;align-items:center;justify-content:center}
      .batt::after{content:"";position:absolute;right:-4.5px;top:4px;width:3px;height:7px;border-radius:0 2px 2px 0;background:var(--c)}
      .fill{position:absolute;left:1.5px;top:1.5px;bottom:1.5px;width:0;border-radius:2px;background:color-mix(in srgb,var(--c) 40%,transparent)}
      .fill.low{background:rgba(229,57,53,.55)}
      .batt span{position:relative;font-size:11px;line-height:1;color:var(--primary-text-color)}
      .node.missing .batt{border-color:var(--disabled-text-color,#777)}
      .node.missing .batt::after{background:var(--disabled-text-color,#777)}
      .badge{display:none;position:absolute;right:-2px;top:-2px;width:20px;height:20px;border-radius:50%;background:#e53935;color:#fff;font-size:12px;line-height:20px;text-align:center}
      .node.missing .ring{border:2px dashed var(--disabled-text-color,#777);background:var(--card-background-color,#1c1c1c)}
      .node.missing ha-icon{color:var(--disabled-text-color,#777)}
      .node.missing .value{color:#e53935;font-size:11px}
      .node.missing .badge{display:block}
      .hint,.notice{font-size:12px;line-height:1.5;color:var(--secondary-text-color);padding-top:6px}
      .notice{font-size:13px;padding:0 0 8px;text-align:center}
      /* On a tablet the house is much larger than the circles: they grow with it. */
      :host(.embedded){container-type:inline-size}
      /* The circles' middle is at 175 of 330 and the card pads its top more: moved to the card's middle. */
      :host(.embedded) .stage{transform:translateY(calc(-3.03% - 3px))}
      @container (min-width:680px){:host(.embedded) .stage{zoom:1.3}}
      :host(.embedded) .hint,:host(.embedded) .notice{padding-left:calc(var(--chips-inset,16px) - 16px)}
    </style>
    <ha-card><div class="head"><div class="title" id="title"></div><div class="chip" id="chip" hidden></div></div>
      <div class="stage" id="stage"><svg viewBox="0 0 400 330">
        <defs>${Object.entries(FLOW_PATHS).map(gradient).join("")}</defs>
        ${Object.entries(FLOW_PATHS).map(path).join("")}${Object.entries(FLOW_CROSS).map(cross).join("")}</svg>
        ${FLOW_NODES.map(node).join("")}</div>
      <div class="notice" id="notice" hidden></div><div class="hint" id="hint" hidden></div></ha-card>`;
    this.$ = (id) => root.getElementById(id);
    const entity = { solar: "solar_entity", grid: "grid_entity", home: "load_entity", battery: "battery_entity" };
    for (const [id, key] of Object.entries(entity)) {
      this.$(`node-${id}`).addEventListener("click", () => this.dispatchEvent(
        new CustomEvent("hass-more-info", { detail: { entityId: this.config[key] }, bubbles: true, composed: true })
      ));
    }
  }

  set hass(hass) {
    const t = this.text;
    const m = flowModel(hass, this.config);
    this.$("title").textContent = this.config.title || "";
    // The circles always show; without readings they read "--", nothing moves
    // and one line under them says why.
    this.$("notice").hidden = m.ready;
    this.$("chip").hidden = !m.ready || !m.offGrid || this.config.embedded === true;
    this.$("chip").textContent = t.offGrid || "";
    if (!m.ready) {
      this.$("notice").textContent = t.unavailable || "";
      this.$("hint").hidden = true;
      const label = { solar: t.solar, grid: t.grid, home: t.load, battery: t.batteryShort };
      for (const id of FLOW_NODES) {
        this.$(`node-${id}`).classList.remove("missing", "active");
        this.$(`value-${id}`).textContent = "--";
        this.$(`label-${id}`).textContent = label[id] || "";
      }
      this.$("soc").textContent = "";
      this.$("fill").style.width = "0";
      this.$("ring-home").classList.remove("mix");
      for (const id of Object.keys(FLOW_PATHS)) {
        this.$(`base-${id}`).classList.remove("missing");
        this.$(`line-${id}`).classList.remove("on");
        this.$(`dot-${id}`).classList.remove("on");
      }
      for (const id of Object.keys(FLOW_CROSS)) this.$(`cross-${id}`).classList.remove("on");
      return;
    }
    const w = m.watts;
    const split = flowSplit(w);
    const arrow = (v, plus, minus) => (v > 0 ? `${plus} ` : v < 0 ? `${minus} ` : "");
    const value = {
      solar: formatWatts(w.solar),
      grid: `${arrow(w.grid, "→", "←")}${formatWatts(w.grid)}`,
      home: formatWatts(w.load),
      battery: `${arrow(w.battery, "↑", "↓")}${formatWatts(w.battery)}`,
    };
    const label = {
      solar: t.solar,
      grid: m.missing.grid || !w.grid ? t.grid : w.grid > 0 ? t.importing : t.exporting,
      home: t.load,
      battery: m.missing.battery || !w.battery ? t.batteryShort : w.battery > 0 ? t.discharging : t.charging,
    };
    for (const id of FLOW_NODES) {
      const missing = Boolean(m.missing[id]);
      this.$(`node-${id}`).classList.toggle("missing", missing);
      // A node carrying power glows; an idle or missing one stays plain.
      const power = { solar: w.solar, grid: w.grid, home: w.load, battery: w.battery }[id];
      this.$(`node-${id}`).classList.toggle("active", !missing && power !== 0);
      this.$(`value-${id}`).textContent = missing ? (id === "grid" ? t.gridMissing : t.batteryMissing) : value[id];
      this.$(`label-${id}`).textContent = label[id] || "";
    }
    const soc = m.soc === null || m.missing.battery ? null : Math.min(Math.max(m.soc, 0), 100);
    this.$("soc").textContent = soc === null ? "" : `${Math.round(soc)}%`;
    this.$("fill").style.width = soc === null ? "0" : `calc((100% - 3px) * ${soc / 100})`;
    this.$("fill").classList.toggle("low", soc !== null && soc < 20);
    for (const [id, p] of Object.entries(FLOW_PATHS)) {
      // gridBattery shares the batteryGrid line and runs backwards.
      const forward = split[id] || 0;
      const backward = id === "batteryGrid" ? split.gridBattery : 0;
      const watts = forward || backward;
      const dead = Boolean(m.missing[p.from] || m.missing[p.to]);
      this.$(`base-${id}`).classList.toggle("missing", dead);
      const on = watts > 0 && !dead;
      const color = FLOW_COLORS[backward && !forward ? "grid" : p.from];
      const line = this.$(`line-${id}`), dot = this.$(`dot-${id}`), move = this.$(`move-${id}`);
      line.classList.toggle("on", on);
      dot.classList.toggle("on", on);
      dot.setAttribute("fill", color);
      move.setAttribute("keyPoints", backward && !forward ? "1;0" : "0;1");
      // More power, faster dots: about 3 s per pass at 100 W, 1 s from ~5 kW.
      const kw = watts / 1000;
      move.setAttribute("dur", `${Math.max(1, 3.2 - Math.log10(1 + kw * 10) * 1.2).toFixed(2)}s`);
    }
    for (const id of Object.keys(FLOW_CROSS)) this.$(`cross-${id}`).classList.toggle("on", Boolean(m.missing[id]));
    // The home ring shows where its power comes from, like HA's energy card.
    const ring = this.$("ring-home");
    const parts = [["solar", split.solarHome], ["battery", split.batteryHome], ["grid", split.gridHome]];
    const total = parts.reduce((sum, [, v]) => sum + v, 0);
    ring.classList.toggle("mix", total > 0);
    if (total > 0) {
      let at = 0;
      const stops = parts.filter(([, v]) => v > 0).map(([k, v]) => {
        const from = at;
        at += (v / total) * 100;
        return `${FLOW_COLORS[k]} ${from.toFixed(1)}% ${at.toFixed(1)}%`;
      });
      ring.style.setProperty("--mix", `conic-gradient(${stops.join(",")})`);
    }
    const hint = this.$("hint");
    hint.hidden = m.negativeLoad === null;
    if (m.negativeLoad !== null) {
      hint.textContent = (t.negativeLoad || "").replace("{value}", `-${Math.round(-m.negativeLoad)} W`);
    }
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, min_rows: 6 }; }
}

// House energy flow: a picture of a home with glowing pulses running along the
// cables in it, the three live readings above the roof and the battery state in
// front. The light and dark pictures (frontend/house/) are the same render of one scene;
// the routes below are in a 1170 x 906 frame over them and follow the cables drawn in it.
const HOUSE_W = 1170;
const HOUSE_H = 906;
const HOUSE_HUB = [726, 516]; // where the wall cable splits
// Each route is drawn from its first point to its last ("forward"). The grid
// cable has two pieces, above and below the box on the wall.
const HOUSE_ROUTES = {
  solar: [[[837, 395], [817, 427], [831, 450], [831, 498]]], // roof panels down to the inverter above the battery
  grid: [[[726, 424], [726, 437]], [[726, 470], HOUSE_HUB]], // grid entry, through the wall box, to the junction
  inverter: [[[797, 527], HOUSE_HUB]], // inverter out to the junction
  home: [[HOUSE_HUB, [680, 509]]], // junction into the house
};
const HOUSE_DASH = [[30, 0.25], [18, 0.45], [9, 0.8]]; // pulse tail layers: length (of 100), opacity

function routeLength(points) {
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    length += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  }
  return length;
}

// What each cable carries, from the same balanced readings as the flow card.
// direction: 1 = forward along HOUSE_ROUTES, -1 = backward, 0 = idle.
function houseModel(hass, config) {
  const m = flowModel(hass, config);
  if (!m.ready) return m;
  const w = m.watts;
  const sign = (v) => (v >= FLOW_TOLERANCE_W ? 1 : v <= -FLOW_TOLERANCE_W ? -1 : 0);
  // AC side of the inverter: PV plus battery discharge (minus charge).
  const inverter = w.solar + w.battery;
  const routes = {
    solar: { direction: sign(w.solar) > 0 ? 1 : 0, watts: Math.max(w.solar, 0) },
    grid: { direction: m.missing.grid ? 0 : sign(w.grid), watts: Math.abs(w.grid) },
    inverter: { direction: sign(inverter), watts: Math.abs(inverter) },
    home: { direction: sign(w.load) > 0 ? 1 : 0, watts: w.load },
  };
  const battery = m.missing.battery ? "missing" : w.battery < 0 ? "charging" : w.battery > 0 ? "discharging" : "standby";
  const grid = m.missing.grid ? "missing" : w.grid > 0 ? "importing" : w.grid < 0 ? "exporting" : "grid";
  return { ...m, routes, battery, grid };
}

function splitWatts(w) {
  const a = Math.abs(w);
  return a < 1000 ? [String(Math.round(a)), "W"] : [(a / 1000).toFixed(2), "kW"];
}

const HOUSE_VIEW_KEY = "energylib.flowView";

const HOUSE_IMAGES = {
  dark: new URL(`./house/house-dark.webp?v=${encodeURIComponent(VERSION)}`, import.meta.url).href,
  light: new URL(`./house/house-light.webp?v=${encodeURIComponent(VERSION)}`, import.meta.url).href,
};

class EnergyLibHouseFlowCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.text = config.text || {};
    this.speeds = {};
    this.classList.toggle("frameless", Boolean(config.frameless));
    if (this.diagram) this.diagram.setConfig({ ...config, title: "", embedded: true });
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: "open" });
    const pts = (p) => p.map((q) => q.join(",")).join(" ");
    const anim = `<animate attributeName="stroke-dashoffset" dur="2s" repeatCount="indefinite"/>`;
    const piece = (p) => `<polyline class="tube" points="${pts(p)}"/>`
      + `<polyline class="halo" points="${pts(p)}" pathLength="100" stroke-dasharray="34 200" filter="url(#hf-glow)">${anim}</polyline>`
      + HOUSE_DASH.map(([len, alpha]) => `<polyline class="pulse" points="${pts(p)}" pathLength="100"`
        + ` stroke-dasharray="${len} 200" style="opacity:${alpha}">${anim}</polyline>`).join("")
      + `<polyline class="head" points="${pts(p)}" pathLength="100" stroke-dasharray="2.5 200">${anim}</polyline>`;
    const route = ([id, pieces]) => `<g class="route" id="route-${id}">${pieces.map(piece).join("")}</g>`;
    const metric = (id, left) => `<button class="metric" id="metric-${id}" style="left:${left}%">`
      + `<span class="num"><b id="num-${id}"></b><small id="unit-${id}"></small></span><span class="name" id="name-${id}"></span></button>`;
    root.innerHTML = `<style>
      :host{display:block;--flow-a:#1f9d86;--flow-b:#7ef2d6;--head:#fff}
      :host(.light){--flow-a:#0b7f6e;--flow-b:#2ec2a6;--head:#f4fffc}
      ha-card{overflow:hidden;position:relative}
      :host(.frameless) ha-card{background:none;border:none;box-shadow:none;border-radius:0;
        margin:0 calc(-1 * var(--bleed-right,0px)) 0 calc(-1 * var(--bleed-left,0px))}
      /* Edge to edge on a phone: the picture's own dark backdrop fades into the page at
         the top and bottom, and the sides are the screen's edges. */
      :host(.frameless) .stage{max-width:none}
      :host(.frameless) img{-webkit-mask-image:linear-gradient(to bottom,transparent 0,#000 14%,#000 82%,transparent 100%);
        mask-image:linear-gradient(to bottom,transparent 0,#000 14%,#000 82%,transparent 100%)}
      .switch{position:absolute;left:8px;top:8px;z-index:2;width:36px;height:36px;padding:0;border:0;border-radius:50%;cursor:pointer;
        display:flex;align-items:center;justify-content:center;color:var(--secondary-text-color);
        background:color-mix(in srgb,var(--card-background-color,#1c1c1c) 70%,transparent)}
      .switch:hover{color:var(--primary-text-color)} .switch ha-icon{--mdc-icon-size:20px}
      /* Both views stay in the layout, one above the other: the card is always as tall as the
         taller one, so switching moves nothing, and the house spreads over that height. */
      #views{display:grid;position:relative} #house,#diagram{grid-area:1/1;align-self:center;min-width:0}
      #house{align-self:stretch;display:flex;flex-direction:column}
      /* Opacity hides the whole layer at once: HA's ha-card inside the diagram fades its own
         visibility over 0.3 s, so visibility alone left both views showing on quick switches. */
      .under{visibility:hidden;opacity:0;pointer-events:none}
      energylib-flow{display:block;width:100%;--ha-card-border-width:0;--ha-card-box-shadow:none;--ha-card-background:transparent;--ha-card-border-radius:0}
      /* Running state, bottom left of the card in both views; a tap opens its history. */
      .chips{position:absolute;left:12px;bottom:12px;z-index:2;display:flex;gap:6px}
      /* Beside the circles there is room for one above the other only. */
      .chips.stacked{flex-direction:column;align-items:flex-start;max-width:36%}
      .chips.stacked .chip{max-width:100%;box-sizing:border-box} .chip i{flex:none}
      .chips.stacked span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .chip.mode i{background:#ff9f43} .chip.mode{color:var(--primary-text-color);background:color-mix(in srgb,var(--secondary-text-color) 20%,transparent)}
      .chip{display:flex;align-items:center;gap:6px;padding:3px 10px;border:0;border-radius:12px;
        font:inherit;font-size:12px;cursor:pointer;color:var(--c,var(--secondary-text-color));background:color-mix(in srgb,var(--c,#888) 16%,transparent)}
      .chip i{width:7px;height:7px;border-radius:50%;background:currentColor}
      .stage{position:relative;flex:1 1 auto;width:100%;max-width:720px;margin:0 auto;display:flex;flex-direction:column;container-type:inline-size}
      /* The house stands midway between the readings and the state (its ground is the picture's lower fifth). */
      .stage::before{content:"";flex:2 0 0} .stage::after{content:"";flex:1 0 0}
      .pic{position:relative;flex:none;width:100%;aspect-ratio:${HOUSE_W}/${HOUSE_H}}
      img,svg{position:absolute;inset:0;width:100%;height:100%}
      img{object-fit:contain;user-select:none;pointer-events:none}
      .route{display:none} .route.on{display:inline}
      .route polyline{fill:none;stroke-linecap:round;stroke-linejoin:round}
      .tube{stroke:var(--flow-a);stroke-width:5;opacity:.45}
      .halo{stroke:var(--flow-b);stroke-width:18;opacity:.5}
      .pulse{stroke:var(--flow-b);stroke-width:6}
      .route .pulse:nth-of-type(3){stroke:var(--flow-a)}
      .head{stroke:var(--head);stroke-width:8}
      /* Three equal columns about the card's middle, clear of the switch button. */
      .metric{position:absolute;top:2.5%;width:23.4%;height:15%;padding:0;border:0;background:none;color:inherit;
        font:inherit;text-align:center;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px}
      .metric + .metric{border-left:2px dashed color-mix(in srgb,var(--secondary-text-color) 40%,transparent)}
      .num{color:var(--primary-text-color);white-space:nowrap}
      .num b{font-size:clamp(17px,6cqw,30px);font-weight:600} .num small{font-size:12px;margin-left:2px;color:var(--secondary-text-color)}
      .name{font-size:13px;line-height:1.15;max-width:100%;color:var(--secondary-text-color)}
      .metric.missing .name{color:#e53935}
      .battery{position:absolute;left:66%;top:80%;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;
        display:flex;flex-direction:column;gap:2px;text-shadow:0 1px 3px color-mix(in srgb,var(--card-background-color,#000) 80%,transparent)}
      .battery .state{font-size:13px;color:var(--secondary-text-color)}
      .battery.charging .state{color:#38d39f} .battery.discharging .state{color:#ff9f43}
      .battery .line{display:flex;align-items:baseline;gap:10px;white-space:nowrap}
      .battery .soc{font-size:clamp(16px,5cqw,24px);font-weight:600;color:var(--primary-text-color)}
      .battery .pw{font-size:clamp(14px,4.4cqw,20px);font-weight:600;color:var(--primary-text-color)}
      .battery small{font-size:12px;font-weight:400;margin-left:1px;color:var(--secondary-text-color)}
      .battery.missing .state{color:#e53935}
      .notice,.hint{font-size:12px;line-height:1.5;color:var(--secondary-text-color);padding:0 16px 10px}
      .notice{font-size:13px;padding:4px 16px 12px;text-align:center}
      /* The running chip sits bottom left of the card: text under the house starts
         to its right, so the two never overlap. */
      :host(.chip-on){--chips-inset:var(--chips-w,96px)}
      :host(.chip-on) .notice,:host(.chip-on) .hint{padding-left:var(--chips-inset)}
      [hidden]{display:none!important}
      @media (prefers-reduced-motion:reduce){.halo,.pulse,.head{display:none}.tube{opacity:.85}}
    </style>
    <ha-card><button class="switch" id="switch"><ha-icon id="switch-icon"></ha-icon></button>
      <div class="chips" id="chips"><button class="chip" id="chip" hidden><i></i><span id="chip-text"></span></button>
        <button class="chip mode" id="mode" hidden><i></i><span id="mode-text"></span></button></div>
      <div id="views"><div id="house"><div class="stage" id="stage">
        <div class="pic"><img id="img" alt="">
        <svg viewBox="0 0 ${HOUSE_W} ${HOUSE_H}" role="img"><title id="aria"></title>
          <defs><filter id="hf-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter></defs>
          <g id="routes">${Object.entries(HOUSE_ROUTES).map(route).join("")}</g></svg>
        <button class="battery" id="battery"><span class="state" id="bat-state"></span>
          <span class="line"><span class="soc" id="bat-soc"></span><span class="pw" id="bat-pw"></span></span></button></div>
        ${metric("load", 14.9)}${metric("grid", 38.3)}${metric("solar", 61.7)}
      </div>
      <div class="notice" id="notice" hidden></div><div class="hint" id="hint" hidden></div></div>
      <div id="diagram" class="under"></div></div></ha-card>`;
    this.$ = (id) => root.getElementById(id);
    // House picture or the circle diagram: the viewer's choice, kept in this browser.
    let view = "house";
    try { view = localStorage.getItem(HOUSE_VIEW_KEY) === "diagram" ? "diagram" : "house"; } catch (e) { /* no storage */ }
    this.showView(view);
    this.$("switch").addEventListener("click", () => {
      const next = this.view === "house" ? "diagram" : "house";
      try { localStorage.setItem(HOUSE_VIEW_KEY, next); } catch (e) { /* no storage */ }
      this.showView(next);
    });
    const open = (key) => () => this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId: this.config[key] }, bubbles: true, composed: true })
    );
    this.$("metric-load").addEventListener("click", open("load_entity"));
    this.$("metric-grid").addEventListener("click", open("grid_entity"));
    this.$("metric-solar").addEventListener("click", open("solar_entity"));
    this.$("battery").addEventListener("click", open("battery_entity"));
    this.$("chip").addEventListener("click", open("running_entity"));
    this.$("mode").addEventListener("click", open(this.config.mode_select_entity ? "mode_select_entity" : "mode_entity"));
  }

  // Frameless on a phone: reach the screen edges past the page's side padding.
  // Only on a phone-width page; on a tablet the column stays centred.
  bleed() {
    if (!this.config?.frameless) return;
    this.style.removeProperty("--bleed-left");
    this.style.removeProperty("--bleed-right");
    const r = this.getBoundingClientRect();
    const right = window.innerWidth - r.right;
    if (!r.width || window.innerWidth >= 768 || r.left > 24 || right > 24) return;
    this.style.setProperty("--bleed-left", `${Math.max(r.left, 0)}px`);
    this.style.setProperty("--bleed-right", `${Math.max(right, 0)}px`);
  }

  connectedCallback() {
    this.onResize = this.onResize || (() => this.bleed());
    window.addEventListener("resize", this.onResize);
    requestAnimationFrame(() => this.bleed());
  }

  disconnectedCallback() { window.removeEventListener("resize", this.onResize); }

  showView(view) {
    this.view = view;
    const diagram = view === "diagram";
    if (!this.diagram) {
      // The circle card, inside this card, with the same entities and texts.
      this.diagram = document.createElement("energylib-flow");
      this.diagram.setConfig({ ...this.config, title: "", embedded: true });
      this.$("diagram").append(this.diagram);
      if (this._hass) this.diagram.hass = this._hass;
    }
    // Both views share one grid cell and keep their place in it.
    this.$("house").classList.toggle("under", diagram);
    this.$("diagram").classList.toggle("under", !diagram);
    this.$("chips").classList.toggle("stacked", diagram);
    this.$("switch-icon").setAttribute("icon", diagram ? "mdi:home-lightning-bolt-outline" : "mdi:chart-donut");
    const label = diagram ? this.text.toHouse : this.text.toDiagram;
    this.$("switch").setAttribute("aria-label", label || "");
    this.$("switch").title = label || "";
  }

  set hass(hass) {
    this._hass = hass;
    if (this.diagram) this.diagram.hass = hass;
    const t = this.text;
    const dark = Boolean(hass.themes?.darkMode);
    this.classList.toggle("light", !dark);
    const src = HOUSE_IMAGES[dark ? "dark" : "light"];
    if (this.$("img").getAttribute("src") !== src) this.$("img").setAttribute("src", src);
    const m = houseModel(hass, this.config);
    // The house always shows; without readings the values read "--", nothing
    // flows and one line under the picture says why.
    this.$("notice").hidden = m.ready;
    // Running state in HA's words: green on grid, orange off grid, grey otherwise.
    const running = hass.states[this.config.running_entity];
    const runningKnown = running && !["unavailable", "unknown"].includes(running.state);
    this.$("chip").hidden = !m.ready || !runningKnown;
    // The EMS mode in HA's words beside it (the selector's own names).
    const mode = hass.states[this.config.mode_entity];
    const modeKnown = m.ready && mode && !["unavailable", "unknown"].includes(mode.state);
    this.$("mode").hidden = !modeKnown;
    if (modeKnown) {
      this.$("mode-text").textContent = hass.localize?.(`component.energylib.entity.select.home_mode.state.${mode.state}`)
        || (hass.formatEntityState ? hass.formatEntityState(mode) : mode.state);
    }
    this.classList.toggle("chip-on", !this.$("chip").hidden || modeKnown);
    const chipsKey = `${this.$("chip").hidden}|${this.$("chip-text").textContent}|${this.$("mode").hidden}|${this.$("mode-text").textContent}`;
    if (chipsKey !== this.chipsKey) {
      this.chipsKey = chipsKey;
      // Their width side by side, whichever way they stand now: the text wraps alike in both views.
      requestAnimationFrame(() => {
        const chips = [...this.$("chips").children].filter((c) => !c.hidden);
        const width = chips.reduce((sum, c) => sum + c.offsetWidth, 0) + 6 * Math.max(chips.length - 1, 0);
        this.style.setProperty("--chips-w", `${width + 24}px`);
      });
    }
    if (runningKnown) {
      this.$("chip-text").textContent = hass.formatEntityState ? hass.formatEntityState(running) : running.state;
      const color = { on_grid: "#38d39f", off_grid: "#ff9f43" }[running.state];
      if (color) this.$("chip").style.setProperty("--c", color); else this.$("chip").style.removeProperty("--c");
    }
    if (!m.ready) {
      this.$("notice").textContent = t.unavailable || "";
      this.$("hint").hidden = true;
      for (const [id, name] of [["load", t.load], ["grid", t.grid], ["solar", t.solar]]) {
        this.$(`num-${id}`).textContent = "--";
        this.$(`unit-${id}`).textContent = "";
        this.$(`name-${id}`).textContent = name || "";
        this.$(`metric-${id}`).classList.remove("missing");
      }
      this.$("battery").classList.remove("missing");
      this.$("bat-state").textContent = t.batteryShort || "";
      this.$("bat-soc").textContent = "--";
      this.$("bat-pw").textContent = "";
      for (const id of Object.keys(HOUSE_ROUTES)) this.$(`route-${id}`).classList.remove("on");
      this.speeds = {};
      this.$("aria").textContent = `${t.aria || ""} ${t.unavailable || ""}`.trim();
      return;
    }
    const w = m.watts;
    const show = (id, watts, name, missing = false) => {
      const [num, unit] = splitWatts(watts);
      this.$(`num-${id}`).textContent = missing ? "--" : num;
      this.$(`unit-${id}`).textContent = missing ? "" : unit;
      this.$(`name-${id}`).textContent = name || "";
      this.$(`metric-${id}`).classList.toggle("missing", missing);
    };
    show("load", w.load, t.load);
    show("grid", w.grid, m.grid === "missing" ? t.gridMissing : t[m.grid], m.grid === "missing");
    show("solar", w.solar, t.solar);

    for (const k of ["missing", "charging", "discharging"]) this.$("battery").classList.toggle(k, m.battery === k);
    this.$("bat-state").textContent = m.battery === "missing" ? t.batteryMissing : t[m.battery] || "";
    const soc = m.soc === null || m.battery === "missing" ? null : Math.min(Math.max(m.soc, 0), 100);
    this.$("bat-soc").innerHTML = soc === null ? "" : `${Math.round(soc)}<small>%</small>`;
    const [num, unit] = splitWatts(w.battery);
    this.$("bat-pw").innerHTML = m.battery === "missing" ? "" : `${num}<small>${unit}</small>`;

    for (const [id, r] of Object.entries(m.routes)) {
      const group = this.$(`route-${id}`);
      group.classList.toggle("on", r.direction !== 0);
      if (!r.direction) continue;
      // More power, faster pulses: about 1 s per 200 px at 5 kW, 3 s at 100 W.
      // A route's pieces share one speed, so the two grid pieces stay in step.
      const kw = r.watts / 1000;
      const perUnit = Math.max(1, 3.2 - Math.log10(1 + kw * 10) * 1.2) / 200;
      const length = HOUSE_ROUTES[id].reduce((sum, p) => sum + routeLength(p), 0);
      const dur = `${Math.max(0.8, length * perUnit).toFixed(2)}s`;
      const key = `${r.direction}:${dur}`;
      if (this.speeds[id] === key) continue; // rewriting a running animation restarts it
      this.speeds[id] = key;
      for (const a of group.querySelectorAll("animate")) {
        const len = Number(a.parentElement.getAttribute("stroke-dasharray").split(" ")[0]);
        // Every layer shares its leading edge, so the tail trails behind the head.
        // Forward: the dash end runs 0 -> 100 + tail; backward: its start runs 100 -> -tail.
        a.setAttribute("values", r.direction > 0 ? `${len};${len - 134}` : `-100;34`);
        a.setAttribute("dur", dur);
      }
    }
    const summary = [`${t.load} ${formatWatts(w.load)}`, `${m.grid === "missing" ? t.gridMissing : t[m.grid]} ${formatWatts(w.grid)}`,
      `${t.solar} ${formatWatts(w.solar)}`, `${m.battery === "missing" ? t.batteryMissing : t[m.battery]} ${formatWatts(w.battery)}`
      + (soc === null ? "" : ` ${Math.round(soc)}%`)];
    this.$("aria").textContent = `${t.aria || ""} ${summary.join(", ")}`.trim();
    const hint = this.$("hint");
    hint.hidden = m.negativeLoad === null;
    if (m.negativeLoad !== null) {
      hint.textContent = (t.negativeLoad || "").replace("{value}", `-${Math.round(-m.negativeLoad)} W`);
    }
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12, min_rows: 6 }; }
}

// One line on top while the inverter is offline, instead of a warning on every card.
class EnergyLibOfflineBanner extends HTMLElement {
  setConfig(config) {
    this.config = config;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{display:block} :host([hidden]){display:none}
      .box{display:flex;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:var(--ha-card-border-radius,12px);
        background:color-mix(in srgb,var(--warning-color,#ff9800) 14%,var(--card-background-color,#1c1c1c));
        border:1px solid color-mix(in srgb,var(--warning-color,#ff9800) 45%,transparent)}
      ha-icon{color:var(--warning-color,#ff9800);flex:none;margin-top:1px}
      b{display:block;font-size:15px} span{font-size:13px;color:var(--secondary-text-color);line-height:1.5}
    </style><div class="box" role="status"><ha-icon icon="mdi:lan-disconnect"></ha-icon>
      <div><b id="title"></b><span id="since"></span> <span id="help"></span></div></div>`;
    this.hidden = true;
  }

  set hass(hass) {
    const t = this.config.text || {};
    const ids = this.config.entities || [];
    const states = ids.map((id) => hass.states[id]);
    const offline = states.length > 0 && states.every((s) => !s || s.state === "unavailable");
    this.hidden = !offline;
    // HA asks hidden cards for no space: report it so the grid closes the gap.
    this.style.display = offline ? "" : "none";
    if (!offline) { this.lastSeen = undefined; return; }
    const root = this.shadowRoot;
    root.getElementById("title").textContent = t.title || "";
    root.getElementById("help").textContent = t.help || "";
    // "Last online": the last real reading in the recorded history of the first
    // power sensor (two days, values only). Statistics are no good here: they
    // carry the last value through hours HA itself was stopped.
    if (this.lastSeen === undefined && !this.looking && hass.callWS && ids[0]) {
      this.looking = true;
      const start = new Date(Date.now() - 2 * 24 * 3600 * 1000).toISOString();
      hass.callWS({ type: "history/history_during_period", start_time: start, entity_ids: [ids[0]],
        minimal_response: true, no_attributes: true, significant_changes_only: false })
        .then((r) => {
          const rows = (r?.[ids[0]] || []).filter((row) => Number.isFinite(Number(row.s)) && row.s !== "");
          const last = rows[rows.length - 1];
          this.lastSeen = last ? (last.lu ?? last.lc) * 1000 : null;
        })
        .catch(() => { this.lastSeen = null; })
        .finally(() => { this.looking = false; this.showSince(hass); });
    }
    this.showSince(hass);
  }

  showSince(hass) {
    const t = this.config.text || {};
    const lang = hass.locale?.language || hass.language || "en";
    const when = this.lastSeen ? new Date(this.lastSeen).toLocaleString(lang, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    // null: looked, nothing in two days; undefined: still looking.
    const line = when ? (t.since || "").replace("{time}", when) : this.lastSeen === null ? t.long || "" : "";
    this.shadowRoot.getElementById("since").textContent = line ? `${line} ·` : "";
  }

  getCardSize() { return 1; }
  getGridOptions() { return { columns: 12 }; }
}

// Today in one card: how much the home used and roughly where it came from,
// with battery and grid in and out side by side. Values are today's change of
// the energy counters from HA's statistics, refreshed every five minutes.
const TODAY_KEYS = ["load", "solar", "import", "export", "charge", "discharge"];
const TODAY_COLORS = { solar: "#f7b731", battery: "#b28aff", grid: "#5299ff" };

function todaySplit(v) {
  // Meters cannot say which source fed the home: grid import counts first,
  // then battery discharge, and solar covers the rest.
  const load = Math.max(v.load || 0, 0);
  const grid = Math.min(Math.max(v.import || 0, 0), load);
  const battery = Math.min(Math.max(v.discharge || 0, 0), load - grid);
  const solar = Math.max(Math.round((load - grid - battery) * 1000) / 1000, 0); // no 1e-16 leftovers
  const made = Math.max(v.solar || 0, 0);
  const selfUse = made > 0 ? Math.round(Math.min(Math.max((made - Math.max(v.export || 0, 0)) / made, 0), 1) * 100) : null;
  return { load, solar, battery, grid, selfUse };
}

class EnergyLibTodayCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    const t = config.text || {};
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const pair = (id, icon, color, a, b) => `<button class="pair" id="${id}" style="--c:${color}">
        <div class="pair-body"><ha-icon icon="${icon}"></ha-icon>
          <div class="row"><b id="${id}-a">--</b><small>kWh</small></div><span>${a}</span>
          <div class="row"><b id="${id}-b">--</b><small>kWh</small></div><span>${b}</span></div>
        <div class="vbar"><i id="${id}-bar"></i></div></button>`;
    root.innerHTML = `<style>
      :host{display:block}
      ha-card{padding:14px 16px}
      .top{display:flex;align-items:center;gap:8px}
      .top ha-icon{color:#5299ff}
      .big{font-size:30px;font-weight:600;line-height:1} .unit{font-size:13px;color:var(--secondary-text-color)}
      .label{font-size:13px;color:var(--secondary-text-color);margin-left:auto}
      .bar{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:14px 0 10px;background:var(--divider-color,#444)}
      .bar i{display:block;height:100%}
      .src{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--secondary-text-color)}
      .src span{display:flex;align-items:center;gap:4px;white-space:nowrap} .src b{color:var(--primary-text-color);font-size:15px}
      .src ha-icon{--mdc-icon-size:18px}
      .note{font-size:11px;color:var(--secondary-text-color);margin-top:6px;opacity:.8}
      .grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
      .pair{display:flex;justify-content:space-between;gap:8px;padding:12px 14px;border:0;border-radius:var(--ha-card-border-radius,12px);
        background:var(--ha-card-background,var(--card-background-color,#1c1c1c));color:inherit;font:inherit;text-align:left;cursor:pointer;
        box-shadow:var(--ha-card-box-shadow,none);border:var(--ha-card-border-width,1px) solid var(--ha-card-border-color,var(--divider-color,#e0e0e0))}
      .pair ha-icon{color:var(--c);--mdc-icon-size:22px}
      .pair .row{margin-top:8px} .pair b{font-size:22px;font-weight:600} .pair small{font-size:12px;color:var(--secondary-text-color);margin-left:2px}
      .pair span{display:block;font-size:12px;color:var(--secondary-text-color)}
      .pair.wide{grid-column:1/-1} .pair.wide ha-icon{display:block} .pair span span{display:inline}
      .vbar{width:6px;border-radius:3px;background:color-mix(in srgb,var(--c) 30%,transparent);display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden}
      .vbar i{display:block;width:100%;background:var(--c);border-radius:3px;height:0}
      .wide .vbar{display:none}
      .skel b{color:transparent;background:var(--divider-color,#444);border-radius:4px}
      .mode{margin-left:auto;display:flex;align-items:center;gap:6px;padding:3px 10px;border:0;border-radius:12px;font:inherit;font-size:12px;
        cursor:pointer;color:var(--primary-text-color);background:color-mix(in srgb,var(--secondary-text-color) 18%,transparent);white-space:nowrap}
      .mode[hidden]{display:none}
      .mode i{width:7px;height:7px;border-radius:50%;background:#ff9f43}
      .top .label{margin-left:4px}
      /* No battery: no battery source, no battery pair; PV sits beside the grid. */
      :host(.nobattery) #battery,:host(.nobattery) #s-battery-row{display:none}
      :host(.nobattery) .pair.wide{grid-column:auto} :host(.nobattery) .pair.wide .vbar{display:none}
    </style>
    <ha-card class="skel" id="card"><div class="top"><ha-icon icon="mdi:home-lightning-bolt-outline"></ha-icon>
        <span class="big" id="load">--</span><span class="unit">kWh</span><span class="label">${t.todayUse || ""}</span>
        <button class="mode" id="mode" hidden><i></i><span id="mode-text"></span></button></div>
      <div class="bar" id="bar"></div>
      <div class="src">
        <span><ha-icon icon="mdi:solar-power-variant" style="color:${TODAY_COLORS.solar}"></ha-icon><b id="s-solar">--</b>kWh</span>
        <span><ha-icon icon="mdi:transmission-tower" style="color:${TODAY_COLORS.grid}"></ha-icon><b id="s-grid">--</b>kWh</span>
        <span id="s-battery-row"><ha-icon icon="mdi:home-battery" style="color:${TODAY_COLORS.battery}"></ha-icon><b id="s-battery">--</b>kWh</span>
      </div><div class="note">${t.sourceNote || ""}</div></ha-card>
    <div class="grid">
      ${pair("battery", "mdi:home-battery", TODAY_COLORS.battery, t.charge || "", t.discharge || "")}
      ${pair("gridpair", "mdi:transmission-tower", TODAY_COLORS.grid, t.importing || "", t.exporting || "")}
      <button class="pair wide" id="solarpair" style="--c:${TODAY_COLORS.solar}"><div class="pair-body">
        <ha-icon icon="mdi:solar-power-variant"></ha-icon><div class="row"><b id="solarpair-a">--</b><small>kWh</small></div>
        <span><span id="solar-name">${t.todaySolar || ""}</span> <span id="selfuse" style="display:inline"></span></span></div></button>
    </div>`;
    const open = (key) => () => this.config[key] && this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId: this.config[key] }, bubbles: true, composed: true }));
    root.getElementById("battery").addEventListener("click", open("charge_entity"));
    root.getElementById("gridpair").addEventListener("click", open("import_entity"));
    root.getElementById("solarpair").addEventListener("click", open("solar_entity"));
    root.getElementById("card").addEventListener("click", open("load_entity"));
    root.getElementById("mode").addEventListener("click", (e) => {
      e.stopPropagation(); // not the card's own tap (today's use)
      open(this.config.mode_select_entity ? "mode_select_entity" : "mode_entity")();
    });
  }

  connectedCallback() {
    this.timer = setInterval(() => this.refresh(), 5 * 60 * 1000);
    if (this._hass) this.refresh();
  }

  disconnectedCallback() { clearInterval(this.timer); }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this.stale && onScreen(this)) this.refresh();
    this.classList.toggle("nobattery", hass.states[this.config.alarm_entity]?.attributes?.battery_disconnected === true);
    const mode = hass.states[this.config.mode_entity];
    const known = mode && !["unavailable", "unknown"].includes(mode.state);
    const chip = this.shadowRoot.getElementById("mode");
    chip.hidden = !known;
    if (known) {
      this.shadowRoot.getElementById("mode-text").textContent = hass.localize?.(`component.energylib.entity.select.home_mode.state.${mode.state}`)
        || (hass.formatEntityState ? hass.formatEntityState(mode) : mode.state);
    }
    if (first) this.refresh();
  }

  async refresh() {
    const hass = this._hass;
    if (!hass?.callWS || this.loading) return;
    // Not on screen (the copy in a hidden section): fetch once it is shown.
    this.stale = !onScreen(this);
    if (this.stale) return;
    this.loading = true;
    try {
      const values = {};
      await Promise.all(TODAY_KEYS.map(async (key) => {
        const id = this.config[`${key}_entity`];
        if (!id) return;
        try {
          const r = await hass.callWS({ type: "recorder/statistic_during_period", statistic_id: id, calendar: { period: "day" }, types: ["change"] });
          if (Number.isFinite(r?.change)) values[key] = r.change;
        } catch (e) { /* no statistics yet */ }
      }));
      this.render(values);
    } finally {
      this.loading = false;
    }
  }

  render(v) {
    const root = this.shadowRoot;
    const t = this.config.text || {};
    const fmt = (x) => (Number.isFinite(x) ? x.toFixed(1) : "--");
    const set = (id, x) => { root.getElementById(id).textContent = fmt(x); };
    root.getElementById("card").classList.remove("skel");
    const s = todaySplit(v);
    const known = Number.isFinite(v.load);
    set("load", known ? s.load : NaN);
    set("s-solar", known ? s.solar : NaN);
    set("s-grid", known ? s.grid : NaN);
    set("s-battery", known ? s.battery : NaN);
    root.getElementById("bar").innerHTML = known && s.load > 0
      ? ["solar", "battery", "grid"].map((k) => `<i style="width:${(s[k] / s.load) * 100}%;background:${TODAY_COLORS[k]}"></i>`).join("")
      : "";
    set("battery-a", v.charge); set("battery-b", v.discharge);
    set("gridpair-a", v.import); set("gridpair-b", v.export);
    set("solarpair-a", v.solar);
    const ratio = (a, b) => (a + b > 0 ? `${(a / (a + b)) * 100}%` : "0");
    root.getElementById("battery-bar").style.height = ratio(v.charge || 0, v.discharge || 0);
    root.getElementById("gridpair-bar").style.height = ratio(v.import || 0, v.export || 0);
    root.getElementById("selfuse").textContent = s.selfUse === null ? "" : `· ${(t.selfUse || "").replace("{value}", s.selfUse)}`;
  }

  getCardSize() { return 5; }
  getGridOptions() { return { columns: 12 }; }
}

// Today's curve (user request 2026-11-23, after a reference app's energy page): one
// reading at a time, picked with a segmented switch (grid, PV, home, battery),
// today's kWh for it on top and its power from midnight under it. Grid and battery
// keep the integration's signs: import and discharge above zero, export and charge
// below. Power comes from HA's recorded history; energy from long-term statistics.
const CURVE_KEY = "energylib.curveTab";
const CURVE_COLORS = { grid: ["#5299ff", "#38d39f"], solar: ["#f7b731", "#f7b731"], load: ["#38d39f", "#38d39f"], battery: ["#b28aff", "#ff9f43"] };
const CURVE_W = 360;
const CURVE_H = 150;
const CURVE_PAD = { left: 30, right: 8, top: 10, bottom: 20 };
const CURVE_BUCKET_MS = 5 * 60 * 1000;
// Days the date switch goes back (HA keeps ten days of history by default).
const CURVE_DAYS = 10;

function midnight(now = new Date()) {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d;
}

// History rows to 5-minute averages in kW: [{t: ms since midnight, v: kW}].
function curvePoints(rows, start, unit) {
  const scale = ({ W: 0.001, kW: 1, MW: 1000 }[unit] ?? 0.001);
  const buckets = new Map();
  for (const row of rows || []) {
    const value = Number(row.s ?? row.state);
    const when = row.lu !== undefined ? row.lu * 1000 : Date.parse(row.last_updated || row.last_changed);
    if (!Number.isFinite(value) || !Number.isFinite(when)) continue;
    const key = Math.floor(Math.max(when - start, 0) / CURVE_BUCKET_MS);
    const b = buckets.get(key) || { sum: 0, n: 0 };
    b.sum += value * scale;
    b.n += 1;
    buckets.set(key, b);
  }
  return [...buckets.entries()].sort((a, b) => a[0] - b[0])
    .map(([key, b]) => ({ t: key * CURVE_BUCKET_MS + CURVE_BUCKET_MS / 2, v: b.sum / b.n }));
}

// A round axis step for the readings' range (kW).
function curveScale(points) {
  const values = points.map((p) => p.v);
  let max = Math.max(0, ...values);
  let min = Math.min(0, ...values);
  if (max - min < 0.5) max = min + 0.5;
  const raw = (max - min) / 3;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw);
  return { min: Math.floor(min / step) * step, max: Math.ceil(max / step) * step, step };
}

class EnergyLibDayCurveCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.tabs = (config.tabs || []).filter((tab) => tab.power);
    let saved = "";
    try { saved = localStorage.getItem(CURVE_KEY) || ""; } catch (e) { /* no storage */ }
    this.key = this.tabs.some((tab) => tab.key === saved) ? saved : this.tabs[0]?.key;
    // 0 = today, -1 = yesterday, ... (HA keeps about ten days of history by default).
    this.offset = 0;
    this.tileSignature = null; // the tiles are rebuilt below: fill them again
    this.history = {};
    this.energy = {};
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.innerHTML = `<style>
      :host{display:block}
      ha-card{padding:12px 14px 10px}
      /* Two wide tiles a row, as in the reference app (user 11-23): the icon, then the name over
         one line of small text, "今日 2.0 kWh · 卖电". An odd last tile keeps its half width (user 11-23). A tap
         shows that reading's day below. */
      .tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
      .tile{display:flex;align-items:center;gap:10px;min-width:0;padding:10px 12px;border-radius:12px;cursor:pointer;font:inherit;text-align:left;color:inherit;
        border:1px solid transparent;background:color-mix(in srgb,var(--secondary-text-color) 12%,transparent)}
      .tile[aria-selected="true"]{border-color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent)}
      .tile ha-icon{color:var(--c);--mdc-icon-size:24px;flex:none}
      .tile div{display:flex;flex-direction:column;gap:2px;min-width:0}
      .tile > div > b{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .tile span{font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .tile span > * + *{margin-left:4px}
      .tile span i{font-style:normal} .tile span b{font-weight:600;color:var(--primary-text-color)}
      /* Bought / sold, discharged / charged: small, beside the name (the figure line is full). */
      .tile > div > b em{font-style:normal;font-weight:400;font-size:12px;margin-left:6px}
      :host(.nobattery) .tile[data-key="battery"]{display:none}
      .head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:14px 0 6px}
      .head h3{margin:0;font-size:16px;font-weight:600}
      .date{display:flex;align-items:center;gap:2px;border-radius:10px;background:color-mix(in srgb,var(--secondary-text-color) 14%,transparent)}
      .date button{display:flex;border:0;background:none;color:var(--primary-text-color);padding:5px 6px;cursor:pointer;border-radius:10px}
      .date button:disabled{opacity:.3;cursor:default}
      .date ha-icon{--mdc-icon-size:18px}
      .date span{font-size:13px;min-width:64px;text-align:center;white-space:nowrap}
      .sum{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;margin:0 0 4px;min-height:44px}
      .item{display:flex;flex-direction:column}
      .item b{font-size:20px;font-weight:600;line-height:1.1} .item b small{font-size:12px;font-weight:400;margin-left:2px}
      .item span{font-size:12px;color:var(--secondary-text-color)}
      .op{font-size:16px;color:var(--secondary-text-color);align-self:center}
      svg{display:block;width:100%;height:auto;touch-action:pan-y}
      .axis{font-size:10px;fill:var(--secondary-text-color)}
      .gridline{stroke:var(--divider-color,#444);stroke-dasharray:2 3}
      .zero{stroke:var(--secondary-text-color);opacity:.5}
      .tip{font-size:11px;fill:var(--primary-text-color)}
      .empty{font-size:12px;fill:var(--secondary-text-color)}
    </style>
    <ha-card><div class="tiles" role="tablist">${this.tabs.map((tab) => `<button class="tile" role="tab" data-key="${tab.key}"
        style="--c:${(CURVE_COLORS[tab.key] || CURVE_COLORS.load)[0]}"><ha-icon icon="${tab.icon}"></ha-icon>
        <div><b>${tab.label}<em id="way-${tab.key}"></em></b><span id="live-${tab.key}">--</span></div></button>`).join("")}</div>
      <div class="head"><h3 id="title"></h3>
        <div class="date"><button id="prev"><ha-icon icon="mdi:chevron-left"></ha-icon></button><span id="day"></span>
          <button id="next"><ha-icon icon="mdi:chevron-right"></ha-icon></button></div></div>
      <div class="sum" id="sum"></div>
      <svg id="svg" viewBox="0 0 ${CURVE_W} ${CURVE_H}" role="img"><title id="aria"></title></svg></ha-card>`;
    root.querySelectorAll(".tile").forEach((b) => b.addEventListener("click", () => {
      this.key = b.dataset.key;
      try { localStorage.setItem(CURVE_KEY, this.key); } catch (e) { /* no storage */ }
      this.tip = null;
      this.refresh();
    }));
    root.getElementById("prev").addEventListener("click", () => this.go(-1));
    root.getElementById("next").addEventListener("click", () => this.go(1));
    const svg = root.getElementById("svg");
    svg.addEventListener("pointermove", (e) => this.pointAt(e));
    svg.addEventListener("pointerdown", (e) => this.pointAt(e));
    svg.addEventListener("pointerleave", () => { this.tip = null; this.draw(); });
    this.mark();
  }

  go(step) {
    this.offset = Math.min(0, Math.max(-CURVE_DAYS + 1, this.offset + step));
    this.tip = null;
    this.refresh();
  }

  connectedCallback() {
    this.timer = setInterval(() => {
      // Past days do not change; today's figures are fetched again.
      const today = midnight().getTime();
      for (const id of Object.keys(this.history)) if (this.history[id].day === today) delete this.history[id];
      for (const id of Object.keys(this.energy)) if (id.endsWith(`@${today}`)) delete this.energy[id];
      this.refresh();
    }, 5 * 60 * 1000);
    // The phone pages set hass before putting the card on screen: fetch once it is
    // laid out instead of waiting for the next state change.
    requestAnimationFrame(() => { if (this._hass) this.refresh(); });
  }

  disconnectedCallback() { clearInterval(this.timer); }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this.classList.toggle("nobattery", hass.states[this.config.alarm_entity]?.attributes?.battery_disconnected === true);
    if (this.key === "battery" && this.classList.contains("nobattery")) this.key = this.tabs[0]?.key;
    this.tiles();
    // After a failed fetch, try again at most every 15 s (not on every state change while HA
    // is still starting).
    if (first || (this.stale && !(this.retryAt > Date.now()) && onScreen(this))) this.refresh();
    else this.live();
  }

  // The day's energy on each tile (11-23, user: the live watts are on top of the house; the
  // tiles carry the day's kWh, as in the reference app): PV generated, home used, and for the
  // grid and the battery the balance with its direction (net bought / sold, discharged /
  // charged). Follows the date switch.
  tiles() {
    const t = this.config.text || {};
    const start = this.dayStart(), day = start.getTime();
    // Which day the figures are for, before them (user 11-23: "今日"): 今日 / 昨天 / the date.
    let when = t.dayPrefix || t.dayToday || "";
    if (this.offset === -1) when = t.dayYesterday || "";
    else if (this.offset < -1) {
      try { when = start.toLocaleDateString(this._hass?.locale?.language || undefined, { month: "short", day: "numeric" }); } catch (e) { when = ""; }
    }
    const signature = JSON.stringify([when, this.tabs.map((tab) => tab.energy.map((e) => this.energy[`${e.entity}@${day}`]))]);
    if (signature === this.tileSignature) return;
    this.tileSignature = signature;
    for (const tab of this.tabs) {
      const node = this.shadowRoot.getElementById(`live-${tab.key}`);
      if (!node) continue;
      const v = tab.energy.map((e) => this.energy[`${e.entity}@${day}`]);
      const value = v.length === 2 ? (Number.isFinite(v[0]) && Number.isFinite(v[1]) ? v[0] - v[1] : NaN) : v[0];
      const figure = Number.isFinite(value) ? `${Math.abs(value).toFixed(1)} kWh` : "-- kWh";
      // The day, the figure and (grid, battery) its direction, each kept whole: they wrap as
      // whole parts on a narrow phone instead of cutting the name or the figure (11-23).
      const part = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
      const parts = [];
      if (when) parts.push(part("i", when));
      parts.push(part("b", figure));
      node.replaceChildren(...parts);
      const way = this.shadowRoot.getElementById(`way-${tab.key}`);
      way.textContent = "";
      if (v.length === 2 && Number.isFinite(value) && Math.abs(value) >= .05) {
        // grid: import - export; battery: discharge - charge.
        way.textContent = tab.key === "grid" ? (value > 0 ? t.importing : t.exporting) : (value > 0 ? t.discharging : t.charging);
        way.style.color = (CURVE_COLORS[tab.key] || CURVE_COLORS.load)[value < 0 ? 1 : 0];
      }
    }
  }

  tab() { return this.tabs.find((tab) => tab.key === this.key) || this.tabs[0]; }

  dayStart() {
    const d = midnight();
    d.setDate(d.getDate() + this.offset);
    return d;
  }

  mark() {
    this.shadowRoot.querySelectorAll(".tile")
      .forEach((b) => b.setAttribute("aria-selected", String(b.dataset.key === this.key)));
  }

  async refresh() {
    const hass = this._hass;
    const tab = this.tab();
    this.mark();
    if (!hass?.callWS || !tab) return;
    this.stale = !onScreen(this);
    if (this.stale) return;
    this.draw();
    if (this.loading) { this.again = true; return; }
    const start = this.dayStart();
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    const day = start.getTime();
    const today = this.offset === 0;
    const jobs = [];
    const h = this.history[tab.power];
    if (!h || h.day !== day) {
      jobs.push(hass.callWS({
        type: "history/history_during_period", start_time: start.toISOString(), end_time: end.toISOString(), entity_ids: [tab.power],
        minimal_response: true, no_attributes: true, significant_changes_only: false,
      }).then((r) => {
        const unit = hass.states[tab.power]?.attributes?.unit_of_measurement;
        this.history[tab.power] = { day, points: curvePoints(r?.[tab.power], day, unit) };
      // A failed fetch (HA still starting: "unknown command") is not cached as an
      // empty day; the next update tries again.
      }).catch(() => { delete this.history[tab.power]; this.stale = true; this.retryAt = Date.now() + 15000; }));
    }
    for (const id of [...new Set(this.tabs.flatMap((x) => [...x.energy.map((e) => e.entity), x.exportEnergy].filter(Boolean)))]) {
      const key = `${id}@${day}`;
      if (this.energy[key] !== undefined) continue;
      const period = today ? { calendar: { period: "day" } }
        : { fixed_period: { start_time: start.toISOString(), end_time: end.toISOString() } };
      jobs.push(hass.callWS({ type: "recorder/statistic_during_period", statistic_id: id, ...period, types: ["change"] })
        .then((r) => { this.energy[key] = Number.isFinite(r?.change) ? r.change : null; })
        .catch(() => { delete this.energy[key]; this.stale = true; this.retryAt = Date.now() + 15000; }));
    }
    if (!jobs.length) { this.tiles(); return; }
    this.loading = true;
    try { await Promise.all(jobs); } finally { this.loading = false; }
    this.tiles();
    this.draw();
    // Another reading or day was picked meanwhile.
    if (this.again) { this.again = false; this.refresh(); }
  }

  // The live reading extends today's curve between history fetches.
  live() {
    const tab = this.tab();
    const h = tab && this.history[tab.power];
    // Only when this reading changed (hass updates on every entity in HA).
    const state = this._hass.states[tab?.power];
    if (!h || this.offset !== 0 || h.day !== midnight().getTime() || state === this.seen || !onScreen(this)) return;
    this.seen = state;
    const w = stateWatts(this._hass, tab.power);
    if (w === null) return;
    const t = Date.now() - h.day;
    const last = h.points[h.points.length - 1];
    if (last && Math.floor(last.t / CURVE_BUCKET_MS) === Math.floor(t / CURVE_BUCKET_MS)) {
      last.n = (last.n || 1) + 1;
      last.v += (w / 1000 - last.v) / last.n; // running mean of the 5 minutes so far
    } else h.points.push({ t: Math.floor(t / CURVE_BUCKET_MS) * CURVE_BUCKET_MS + CURVE_BUCKET_MS / 2, v: w / 1000, n: 1 });
    this.draw();
  }

  pointAt(e) {
    const svg = this.shadowRoot.getElementById("svg");
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    this.tip = ((e.clientX - rect.left) / rect.width) * CURVE_W;
    this.draw();
  }

  draw() {
    const root = this.shadowRoot;
    const tab = this.tab();
    const t = this.config.text || {};
    if (!tab) return;
    const start = this.dayStart();
    const day = start.getTime();
    // Title and the day: today, yesterday, then the date in HA's language.
    root.getElementById("title").textContent = tab.label;
    const lang = this._hass?.locale?.language || this._hass?.language || undefined;
    let dayText;
    try { dayText = start.toLocaleDateString(lang, { month: "short", day: "numeric" }); } catch (e) { dayText = start.toDateString(); }
    root.getElementById("day").textContent = this.offset === 0 ? (t.dayToday || dayText)
      : this.offset === -1 ? (t.dayYesterday || dayText) : dayText;
    root.getElementById("next").disabled = this.offset === 0;
    root.getElementById("prev").disabled = this.offset <= -CURVE_DAYS + 1;
    // The day's kWh for this reading: one figure, or in, out and their difference.
    const kwh = (id) => this.energy[`${id}@${day}`];
    const fmt = (x) => (Number.isFinite(x) ? x.toFixed(1) : "--");
    const [up, down] = CURVE_COLORS[tab.key] || CURVE_COLORS.load;
    const item = (value, label, color) => `<div class="item"><b style="color:${color}">${fmt(value)}<small>kWh</small></b><span>${label}</span></div>`;
    const e = tab.energy;
    root.getElementById("sum").innerHTML = tab.net && e.length === 2
      ? item(kwh(e[0].entity), e[0].label, up) + `<span class="op">−</span>` + item(kwh(e[1].entity), e[1].label, down)
        + `<span class="op">=</span>` + item(Number.isFinite(kwh(e[0].entity)) && Number.isFinite(kwh(e[1].entity))
          ? kwh(e[0].entity) - kwh(e[1].entity) : NaN, t.dayNet || "Net", "var(--primary-text-color)")
      : e.map((x, i) => item(kwh(x.entity), x.label, i ? down : up)).join("")
        // PV: how much of it the home used itself (generated minus sold), from the today card (11-23).
        + (tab.exportEnergy && Number.isFinite(kwh(e[0]?.entity)) && kwh(e[0].entity) > 0 && Number.isFinite(kwh(tab.exportEnergy))
          ? `<div class="item"><b>${Math.round(Math.min(Math.max((kwh(e[0].entity) - Math.max(kwh(tab.exportEnergy), 0)) / kwh(e[0].entity), 0), 1) * 100)}<small>%</small></b>`
            + `<span>${(t.selfUse || "").replace(/\s*\{value\}\s*%?\s*/, " ").trim()}</span></div>` : "");

    const svg = root.getElementById("svg");
    const ns = "http://www.w3.org/2000/svg";
    const el = (tag, attrs, text) => {
      const node = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const h = this.history[tab.power];
    let points = h && h.day === day ? h.points : null;
    // A past day: the last reading held until midnight.
    const dayMs = 24 * 3600 * 1000;
    if (points?.length && this.offset < 0 && points[points.length - 1].t < dayMs - CURVE_BUCKET_MS) {
      points = [...points, { t: dayMs - CURVE_BUCKET_MS / 2, v: points[points.length - 1].v }];
    }
    svg.replaceChildren(el("title", { id: "aria" }, `${tab.label}: ${t.dayAria || ""}`));
    const { left, right, top, bottom } = CURVE_PAD;
    const plotW = CURVE_W - left - right;
    const plotH = CURVE_H - top - bottom;
    const span = 24 * 3600 * 1000;
    const x = (ms) => left + (ms / span) * plotW;
    for (const hour of [0, 6, 12, 18, 24]) {
      svg.append(el("text", { x: x(hour * 3600000), y: CURVE_H - 5, class: "axis", "text-anchor": hour === 0 ? "start" : hour === 24 ? "end" : "middle" }, `${hour}:00`));
    }
    if (!points) return;
    if (!points.length) {
      svg.append(el("text", { x: CURVE_W / 2, y: CURVE_H / 2, class: "empty", "text-anchor": "middle" }, t.dayNoData || "--"));
      return;
    }
    const { min, max, step } = curveScale(points);
    const y = (v) => top + ((max - v) / (max - min)) * plotH;
    for (let v = min; v <= max + step / 2; v += step) {
      const value = Math.round(v * 100) / 100;
      svg.append(el("line", { x1: left, x2: CURVE_W - right, y1: y(value), y2: y(value), class: value === 0 ? "zero" : "gridline" }),
        el("text", { x: left - 4, y: y(value) + 3, class: "axis", "text-anchor": "end" }, String(value)));
    }
    svg.append(el("text", { x: 2, y: top - 2, class: "axis" }, "kW"));
    // Above zero in one colour, below in the other (import / export, discharge / charge).
    const id = `c${Math.random().toString(36).slice(2, 8)}`;
    const defs = el("defs", {});
    const clip = (name, y1, y2) => {
      const c = el("clipPath", { id: `${id}-${name}` });
      c.append(el("rect", { x: 0, y: y1, width: CURVE_W, height: Math.max(y2 - y1, 0) }));
      defs.append(c);
    };
    clip("up", 0, y(0));
    clip("down", y(0), CURVE_H);
    const grad = (name, color, flip) => {
      const g = el("linearGradient", { id: `${id}-${name}g`, x1: 0, x2: 0, y1: flip ? 1 : 0, y2: flip ? 0 : 1 });
      g.append(el("stop", { offset: 0, "stop-color": color, "stop-opacity": 0.45 }), el("stop", { offset: 1, "stop-color": color, "stop-opacity": 0.02 }));
      defs.append(g);
    };
    grad("up", up, false);
    grad("down", down, true);
    svg.append(defs);
    // History keeps changes only: across a gap the reading held its last value, so
    // the line stays level until just before the next one (no slope over the night).
    const line = points.map((p, i) => {
      const prev = points[i - 1];
      const hold = prev && p.t - prev.t > 2 * CURVE_BUCKET_MS
        ? `L${x(p.t - CURVE_BUCKET_MS).toFixed(1)} ${y(prev.v).toFixed(1)} ` : "";
      return `${hold}${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`;
    }).join(" ");
    const area = `${line} L${x(points[points.length - 1].t).toFixed(1)} ${y(0)} L${x(points[0].t).toFixed(1)} ${y(0)} Z`;
    for (const [name, color] of [["up", up], ["down", down]]) {
      const g = el("g", { "clip-path": `url(#${id}-${name})` });
      g.append(el("path", { d: area, fill: `url(#${id}-${name}g)` }),
        el("path", { d: line, fill: "none", stroke: color, "stroke-width": 1.8, "stroke-linejoin": "round" }));
      svg.append(g);
    }
    if (this.offset === 0) {
      const last = points[points.length - 1];
      svg.append(el("circle", { cx: x(last.t), cy: y(last.v), r: 3.5, fill: last.v < 0 ? down : up, stroke: "var(--card-background-color,#1c1c1c)", "stroke-width": 1.5 }));
    }
    // Tap or hover: the reading at that time.
    if (this.tip !== null && this.tip !== undefined) {
      const ms = ((this.tip - left) / plotW) * span;
      const near = points.reduce((a, b) => (Math.abs(b.t - ms) < Math.abs(a.t - ms) ? b : a));
      const px = x(near.t);
      const hh = Math.floor(near.t / 3600000);
      const mm = String(Math.floor((near.t % 3600000) / 60000)).padStart(2, "0");
      const label = `${hh}:${mm}  ${near.v.toFixed(2)} kW`;
      svg.append(el("line", { x1: px, x2: px, y1: top, y2: top + plotH, stroke: "var(--secondary-text-color)", "stroke-width": 1 }),
        el("circle", { cx: px, cy: y(near.v), r: 3.5, fill: near.v < 0 ? down : up }),
        el("text", { x: Math.min(Math.max(px, left + 40), CURVE_W - right - 40), y: top + 8, class: "tip", "text-anchor": "middle" }, label));
    }
  }

  getCardSize() { return 5; }
  getGridOptions() { return { columns: 12 }; }
}

class EnergyLibAlarmCard extends HTMLElement {
  setConfig(config) { this.config = config; }
  getCardSize() { return 5; }
  set hass(hass) {
    if (!this.config) return;
    const state = hass.states[this.config.entity];
    const lang = hass.locale?.language || hass.language || "en";
    // HA replaces a state object only when that entity changes: skip the rebuild
    // on every unrelated update, which also collapsed sections the user opened.
    if (state === this._state && lang === this._lang) return;
    this._state = state; this._lang = lang;
    const opened = new Set([...this.querySelectorAll("details[open]")].map((d) => d.dataset.key));
    const labels = translate(hass, "alarms");
    // The device reports alarm names in Chinese and English only.
    const zh = language(hass).startsWith("zh");
    const attrs = state?.attributes || {};
    const active = attrs.alarms || [];
    const complete = attrs.complete === true && !["unknown", "unavailable"].includes(state?.state);
    const card = document.createElement("ha-card");
    const body = document.createElement("div");
    body.style.cssText = "padding:16px;line-height:1.6;overflow-wrap:anywhere";
    const add = (parent, tag, value) => {
      const node = document.createElement(tag); node.textContent = value; parent.append(node); return node;
    };
    const status = !state || !attrs.valid_sources || (state.state === "unavailable" && attrs.complete)
      ? labels.offline : !complete ? labels.partial : attrs.unknown_count ? labels.unrecognized
      : active.length ? labels.abnormal : labels.normal;
    add(body, "h3", labels.title).style.marginTop = "0";
    // Active alarms as counts ("Warning 1 · Notice 4") instead of a second heading.
    const count = (sev) => active.filter((a) => a.severity === sev).length;
    const counts = ["fault", "warning", "info", "unknown"].filter((sev) => count(sev)).map((sev) => `${labels[sev]} ${count(sev)}`);
    add(body, "strong", complete && !active.length ? labels.allClear
      : active.length && complete && !attrs.unknown_count ? counts.join(" · ") : status);
    const time = (value) => value ? new Date(value).toLocaleString(language(hass)) : "—";
    const item = (parent, alarm, history = false) => {
      const block = document.createElement("div");
      block.style.cssText = "border-left:3px solid var(--divider-color);padding:6px 10px;margin:8px 0";
      add(block, "strong", `${labels[alarm.severity] || labels.unknown} · ${zh ? alarm.name : alarm.name_en || alarm.name}`);
      const technical = document.createElement("details");
      technical.dataset.key = `${history ? "h" : "a"}:${alarm.alarm_id}:${alarm.first_seen}`;
      technical.open = opened.has(technical.dataset.key);
      add(technical, "summary", `${labels.first}: ${time(alarm.first_seen)}`);
      add(technical, "div", `FC${String(alarm.function).padStart(2, "0")} · Unit ${alarm.source} · 0x${alarm.code}`);
      add(technical, "div", `${labels.last}: ${time(alarm.last_seen)}`);
      block.append(technical);
      if (alarm.stale || !complete) add(block, "small", labels.stale);
      if (history) add(block, "div", alarm.cleared_at ? `${labels.cleared}: ${time(alarm.cleared_at)}` : labels.pending);
      parent.append(block);
    };
    // Faults and warnings open; notices folded under one line.
    active.filter((a) => a.severity !== "info").forEach((alarm) => item(body, alarm));
    const notices = active.filter((a) => a.severity === "info");
    if (notices.length) {
      const fold = document.createElement("details");
      fold.dataset.key = "notices";
      fold.open = opened.has("notices");
      add(fold, "summary", `${labels.info} · ${notices.length}`);
      notices.forEach((alarm) => item(fold, alarm));
      body.append(fold);
    }
    const details = document.createElement("details");
    details.dataset.key = "history";
    details.open = opened.has("history");
    add(details, "summary", `${labels.history} · ${(attrs.history || []).length}`);
    const records = document.createElement("div");
    records.style.cssText = "max-height:400px;overflow:auto";
    (attrs.history || []).forEach((alarm) => item(records, alarm, true));
    details.append(records); add(details, "p", labels.note).style.cssText = "color:var(--secondary-text-color);font-size:12px";
    body.append(details); card.append(body); this.replaceChildren(card);
  }
}

class EnergyLibRemoteTaskCard extends HTMLElement {
  setConfig(config) { this.config = config; }
  getCardSize() { return 3; }
  set hass(hass) { this._hass = hass; this.render(); }
  connectedCallback() {
    this.timer = setInterval(() => { if (onScreen(this)) this.render(); }, 1000);
    this.render();
  }
  disconnectedCallback() { clearInterval(this.timer); this.timer = null; }
  render() {
    if (!this.config || !this._hass) return;
    const names = translate(this._hass, "remoteTask");
    const state = this._hass.states[this.config.entity];
    const attrs = state?.attributes || {};
    const status = state?.state || "unavailable";
    const card = document.createElement("ha-card");
    const body = document.createElement("div"); body.style.cssText = "padding:16px;line-height:1.8";
    const add = (tag, text) => { const el = document.createElement(tag); el.textContent = text; body.append(el); };
    add("strong", names[status] || status);
    const power = (value) => typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)} kW` : "—";
    add("div", `${names.target}: ${power(attrs.target_power_kw)}`);
    add("div", `${names.actual}: ${power(attrs.actual_power_kw)}`);
    if (attrs.end_time) {
      const end = Date.parse(attrs.end_time);
      if (Number.isFinite(end)) {
        add("div", `${names.endsAt}: ${new Date(end).toLocaleString(language(this._hass))}`);
        const seconds = Math.max(0, Math.ceil((end - Date.now()) / 1000));
        add("div", seconds > 0
          ? `${names.remaining}: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
          : names.waitingExit);
      }
    }
    card.append(body); this.replaceChildren(card);
  }
}

// The EMS mode, as in the app: a row of mode icons (the current one marked)
// and one line on what the picked mode does. A tap writes at once; the backend
// serializes, writes and reads back. The battery/grid settings are Home
// Assistant's own tiles below this card (one design for every device). The modes
// follow what the device offers, so another device profile shows its own.
const MODE_ICONS = {
  self_consumption: "mdi:home-lightning-bolt-outline", time_of_use: "mdi:clock-outline", backup: "mdi:battery-lock",
  pv_charge_priority: "mdi:solar-power-variant", feed_in_priority: "mdi:transmission-tower-export", peak_shaving: "mdi:chart-bell-curve",
  dynamic_tariff: "mdi:currency-usd", other: "mdi:cog-outline",
};
// Set by other functions (manual dispatch, the tariff app, grid support): not a choice here.
const LOCKED_MODES = ["dynamic_tariff", "remote", "voltage_regulation"];
const UNKNOWN_STATES = ["unknown", "unavailable"];

class EnergyLibModeCard extends HTMLElement {
  // The word the selector uses for a standard mode key: its own translation (the
  // backend builds the options from it), else the mode sensor's word.
  static modeWord(hass, config, key) {
    const own = hass.localize?.(`component.energylib.entity.select.home_mode.state.${key}`);
    if (own) return own;
    const sensor = hass.states[config.mode_entity];
    return sensor && hass.formatEntityState ? hass.formatEntityState(sensor, key) : undefined;
  }

  // Exactly the modes the selector offers, in its order: [{id, label, key}]. The
  // options are words in HA's language; matching them to the standard keys gives the
  // icon, the line and set_ems_mode. An option without a standard key (another
  // profile's own mode, or words in another language) is still offered and written
  // by its word.
  static modes(hass, config) {
    const offered = hass.states[config.select_entity]?.attributes?.options;
    if (!Array.isArray(offered)) return [];
    const keys = hass.states[config.mode_entity]?.attributes?.options || Object.keys(MODE_ICONS);
    return offered.map((label) => {
      const key = keys.find((k) => EnergyLibModeCard.modeWord(hass, config, k) === label);
      return { id: key || `option:${label}`, label, key };
    });
  }

  // The offered mode the device is in now (null when it is in none of them).
  static currentId(hass, config, modes) {
    const key = hass.states[config.mode_entity]?.state;
    const label = hass.states[config.select_entity]?.state;
    return (modes.find((m) => m.key && m.key === key) || modes.find((m) => m.label === label))?.id ?? null;
  }

  setConfig(config) {
    this.config = config;
    this.picked = null;  // the mode being written
    this.busy = false;
    this.message = "";
    this.queue = Promise.resolve();
    this.root = this.shadowRoot || this.attachShadow({ mode: "open" });
    if (this.wired) return;
    this.wired = true;
    this.root.addEventListener("click", (e) => this.onClick(e));
  }

  set hass(hass) {
    this._hass = hass;
    // Redraw only when one of the card's own entities changed (a pending card has none).
    const key = [this.config.mode_entity, this.config.select_entity].map((id) => hass.states[id]).concat(hass.locale?.language);
    if (this._key && key.every((v, i) => v === this._key[i])) return;
    this._key = key;
    this.render();
  }

  get texts() { return translate(this._hass, "dashboard"); }

  onClick(e) {
    const el = e.composedPath().find((n) => n.dataset && n.dataset.mode);
    if (!el || this.busy) return;
    this.message = "";
    this.picked = el.dataset.mode;
    this.render();
    this.queue = this.queue.then(() => this.write());
  }

  async write() {
    const hass = this._hass, t = this.texts, c = this.config;
    const modes = EnergyLibModeCard.modes(hass, c);
    const mode = modes.find((m) => m.id === this.picked);
    if (!mode || mode.id === EnergyLibModeCard.currentId(hass, c, modes)) { this.picked = null; this.render(); return; }
    this.busy = true;
    this.message = t.applying;
    this.render();
    try {
      if (mode.key) {
        await hass.callService("energylib", "set_ems_mode", { device_id: c.device_id, mode: mode.key });
      } else {
        await hass.callService("select", "select_option", { entity_id: c.select_entity, option: mode.label });
      }
      this.message = t.applied;
      this.failed = false;
    } catch (error) {
      // Show the device's mode again, with the reason.
      this.message = error?.message || String(error);
      this.failed = true;
    } finally {
      this.picked = null;
      this.busy = false;
      this._key = null;
      this.render();
    }
  }

  render() {
    if (!this.config || !this._hass || !this.root) return;
    const hass = this._hass, t = this.texts, c = this.config;
    const sensor = hass.states[c.mode_entity];
    const offline = !sensor || UNKNOWN_STATES.includes(sensor.state);
    const locked = LOCKED_MODES.includes(sensor?.state);
    const nowName = sensor && EnergyLibModeCard.modeWord(hass, c, sensor.state) || sensor?.state;
    const list = EnergyLibModeCard.modes(hass, c);
    const current = EnergyLibModeCard.currentId(hass, c, list);
    const picked = this.picked || current;
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
    const modes = list.map((m) => `<button class="mode${m.id === picked ? " picked" : ""}" data-mode="${esc(m.id)}"
        ${offline || locked || this.busy ? "disabled" : ""} aria-pressed="${m.id === picked}"><span class="ic"><ha-icon icon="${MODE_ICONS[m.key] || MODE_ICONS.other}"></ha-icon>${m.id === current ? '<i class="dot"></i>' : ""}</span>
        <span>${esc(m.label)}</span></button>`).join("");
    const pickedKey = list.find((m) => m.id === picked)?.key;
    const desc = c.pending ? t.modePending
      : locked ? (t.modeLocked || "").replace("{mode}", nowName) : (pickedKey && (t.modeDesc || {})[pickedKey]) || "";
    const now = c.pending ? t.pending : offline ? "--" : nowName;
    this.root.innerHTML = `<style>
      :host{display:block} ha-card{padding:16px}
      .head{display:flex;align-items:center;gap:8px;margin-bottom:12px} .head h3{margin:0;font-size:16px;font-weight:600}
      .head .now{margin-left:auto;font-size:13px;color:var(--secondary-text-color)}
      .modes{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
      .mode{display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 4px;border-radius:12px;cursor:pointer;font:inherit;font-size:12px;
        color:var(--primary-text-color);background:var(--secondary-background-color);border:1.5px solid transparent;text-align:center;line-height:1.3}
      .mode.picked{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 12%,var(--secondary-background-color))}
      .mode:disabled{opacity:.5;cursor:default}
      .ic{position:relative;display:flex} .ic ha-icon{--mdc-icon-size:26px}
      .dot{position:absolute;right:-5px;top:-2px;width:8px;height:8px;border-radius:50%;background:#38d39f}
      .desc{font-size:13px;color:var(--secondary-text-color);margin:8px 2px 0;line-height:1.5;min-height:1.5em}
      .msg{font-size:13px;margin-top:8px;color:var(--secondary-text-color)} .msg.err{color:var(--error-color,#d32f2f)}
      [hidden]{display:none!important}
    </style><ha-card>
      <div class="head"><ha-icon icon="mdi:cog-transfer"></ha-icon><h3>${esc(t.mode)}</h3><span class="now">${esc(now)}</span></div>
      ${c.pending ? "" : `<div class="modes" role="group" aria-label="${esc(t.mode)}">${modes}</div>`}
      <div class="desc" id="desc">${esc(desc)}</div>
      <div class="msg${this.failed ? " err" : ""}" id="msg" role="status" ${this.message ? "" : "hidden"}>${esc(this.message)}</div>
    </ha-card>`;
  }

  getCardSize() { return 4; }
  getGridOptions() { return { columns: 12 }; }
}

// The time-of-use plan, edited on the page as in the app: one row per period,
// "+ Add period", the weekdays and the mode outside the periods. Changes are a
// draft until "Save to device" writes the whole plan once (user decision 2026-11-19),
// through energylib.set_tou_schedule: the same backend path as the integration's own form
// (checked, written, read back; validity dates kept; the EMS mode is left as it
// is, so switching to the schedule stays a choice in the mode card).
// The checks here only explain mistakes early; the backend decides.
const TOU_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const TOU_OUTSIDE = ["self_consumption", "backup", "pv_charge_priority", "feed_in_priority"];
const TOU_MAX_PER_TYPE = 6;

function touMinutes(value) {
  const m = /^(\d\d):(\d\d)$/.exec(value || "");
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

class EnergyLibTouCard extends HTMLElement {
  // The plan as the sensor reports it (empty when not read yet).
  static plan(hass, config) {
    const a = hass.states[config.entity]?.attributes || {};
    return {
      periods: (a.periods || []).map((p) => ({ type: p.type, start: p.start, end: p.end, power: Number(p.power) })),
      weekdays: a.weekdays ? [...a.weekdays] : [...TOU_DAYS],
      outside_mode: a.outside_mode,
    };
  }

  // Why a period cannot go into the plan (a text key), or null. `skip` is the
  // index of the period being edited.
  static problem(plan, period, skip, maxPower, maxPerType = TOU_MAX_PER_TYPE, crossMidnight = false) {
    const start = touMinutes(period.start), end = touMinutes(period.end);
    if (start === null || end === null || end === start || (end < start && !crossMidnight)) return "errTime";
    const power = Number(period.power);
    if (!Number.isFinite(power) || power < 0 || (Number.isFinite(maxPower) && power > maxPower)) return "errPower";
    const others = plan.periods.filter((_, i) => i !== skip);
    // A period across midnight is two spans of the day.
    const spans = (p) => {
      const a = touMinutes(p.start), b = touMinutes(p.end);
      return b > a ? [[a, b]] : [[a, 1440], [0, b]];
    };
    const mine = spans(period);
    if (others.some((o) => spans(o).some(([a, b]) => mine.some(([c, d]) => c < b && a < d)))) return "errOverlap";
    if (others.filter((o) => o.type === period.type).length >= maxPerType) return "errCount";
    return null;
  }

  static same(a, b) {
    const norm = (p) => JSON.stringify({ periods: p.periods, weekdays: [...p.weekdays].sort(), outside_mode: p.outside_mode ?? null });
    return norm(a) === norm(b);
  }

  setConfig(config) {
    this.config = config;
    this.draft = null; // the edited plan, written by "Save to device"
    this.form = null; // the period being added or edited: {index, type, start, end, power}
    this.queue = Promise.resolve();
    this.root = this.shadowRoot || this.attachShadow({ mode: "open" });
    if (this.wired) return;
    this.wired = true;
    this.root.addEventListener("click", (e) => this.onClick(e));
    this.root.addEventListener("input", (e) => this.onInput(e));
    this.root.addEventListener("change", (e) => this.onInput(e));
  }

  set hass(hass) {
    this._hass = hass;
    const key = [this.config.entity, this.config.mode_entity, this.config.select_entity].map((id) => hass.states[id]).concat(hass.locale?.language);
    if (this._key && key.every((v, i) => v === this._key[i])) return;
    this._key = key;
    // The saved plan shows until the sensor reports the plan again (equal or, if
    // the device normalised a value, as the device has it), or for 6 minutes at
    // most (the sensor reads the plan every 5).
    if (this.saved && (hass.states[this.config.entity] !== this.savedFrom || Date.now() - this.savedAt > 360000
      || EnergyLibTouCard.same(EnergyLibTouCard.plan(hass, this.config), this.saved))) this.saved = null;
    // Typing in the period form is not interrupted by state updates.
    if (!this.form) this.render();
  }

  get texts() { return translate(this._hass, "dashboard"); }
  get shown() { return this.draft || this.saved || EnergyLibTouCard.plan(this._hass, this.config); }
  // The rated power: the maximum of the first of these numbers that is live (the
  // remote target power is disabled while remote access is off; the export limit
  // has the same range).
  get maxPower() {
    for (const id of this.config.max_power_entities || []) {
      const max = Number(this._hass.states[id]?.attributes?.max);
      if (Number.isFinite(max) && max > 0) return max;
    }
    return NaN;
  }

  // What this model's plan offers (the sensor says; other models have it all).
  get caps() {
    const a = this._hass?.states[this.config.entity]?.attributes || {};
    return {
      power: a.power_supported !== false,
      days: a.weekdays_supported !== false,
      perType: Number(a.max_per_type) || TOU_MAX_PER_TYPE,
      crossMidnight: a.cross_midnight === true,
    };
  }

  edit() {
    if (!this.draft) this.draft = JSON.parse(JSON.stringify(this.shown));
    return this.draft;
  }

  // Something differs from the plan on the device.
  get dirty() {
    return !!this.draft && !EnergyLibTouCard.same(this.draft, this.saved || EnergyLibTouCard.plan(this._hass, this.config));
  }

  onClick(e) {
    const el = e.composedPath().find((n) => n.dataset && Object.keys(n.dataset).length);
    if (!el || this.busy) return;
    const d = el.dataset;
    if (d.action === "fold") {
      this.open = !this.open;
    } else if (d.action === "add") {
      this.form = { index: -1, type: "charge", start: "", end: "", power: "" };
    } else if (d.action === "edit") {
      const p = this.shown.periods[Number(d.index)];
      this.form = { index: Number(d.index), ...p };
    } else if (d.action === "delete") {
      this.edit().periods.splice(Number(d.index), 1);
    } else if (d.action === "type" && this.form) {
      this.form.type = d.type;
    } else if (d.action === "ok" && this.form) {
      const caps = this.caps;
      const plan = this.shown, period = { type: this.form.type, start: this.form.start, end: this.form.end, power: caps.power ? Number(this.form.power) : 0 };
      const problem = EnergyLibTouCard.problem(plan, period, this.form.index, caps.power ? this.maxPower : NaN, caps.perType, caps.crossMidnight);
      if (problem) { this.formError = problem; this.render(); return; }
      const draft = this.edit();
      if (this.form.index >= 0) draft.periods[this.form.index] = period; else draft.periods.push(period);
      draft.periods.sort((a, b) => touMinutes(a.start) - touMinutes(b.start));
      this.form = null;
    } else if (d.action === "close") {
      this.form = null;
    } else if (d.day) {
      const days = this.edit().weekdays;
      const i = days.indexOf(d.day);
      if (i >= 0) days.splice(i, 1); else days.push(d.day);
    } else if (d.action === "save") {
      this.save();
      return;
    } else if (d.action === "cancel") {
      this.draft = null; this.form = null;
    } else {
      return;
    }
    this.formError = null;
    this.message = "";
    this.render();
  }

  onInput(e) {
    const el = e.composedPath?.()[0] || e.target;
    if (el?.dataset?.field && this.form) this.form[el.dataset.field] = el.value;
    if (el?.dataset?.outside !== undefined && e.type === "change") {
      this.edit().outside_mode = el.value;
      this.message = "";
      this.render();
    }
  }

  // Write the whole edited plan once (one write at a time).
  save() {
    if (!this.dirty) return this.queue;
    this.queue = this.queue.then(() => this.write());
    return this.queue;
  }

  async write() {
    const hass = this._hass, t = this.texts, plan = this.draft;
    if (!plan) return;
    if (!plan.weekdays.length) { this.message = t.errDays; this.failed = true; this.render(); return; }
    this.busy = true; this.message = t.applying; this.failed = false; this.render();
    const data = {
      device_id: this.config.device_id,
      periods: plan.periods.map((p) => ({ type: p.type, start: p.start, end: p.end, power: Number(p.power) })),
      weekdays: TOU_DAYS.filter((d) => plan.weekdays.includes(d)),
    };
    if (plan.outside_mode) data.outside_mode = plan.outside_mode;
    try {
      await hass.callService("energylib", "set_tou_schedule", data);
      this.saved = { periods: data.periods, weekdays: data.weekdays, outside_mode: plan.outside_mode };
      this.savedFrom = hass.states[this.config.entity];
      this.savedAt = Date.now();
      this.draft = null;
      this.message = t.applied;
    } catch (error) {
      // Keep the draft so it can be corrected and saved again.
      this.message = error?.message || String(error);
      this.failed = true;
    } finally {
      this.busy = false; this._key = null; this.render();
    }
  }

  modeWord(key) {
    return this._hass.localize?.(`component.energylib.entity.select.home_mode.state.${key}`) || key;
  }

  render() {
    if (!this.config || !this._hass || !this.root) return;
    const hass = this._hass, t = this.texts, c = this.config;
    const sensor = hass.states[c.entity];
    const offline = !sensor || ["unknown", "unavailable"].includes(sensor.state);
    const remote = sensor?.state === "remote";
    const locked = offline || remote || this.busy;
    const plan = this.shown;
    const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
    const kind = (type) => (type === "charge" ? t.charge : t.discharge);
    const caps = this.caps;
    const rows = plan.periods.map((p, i) => `<div class="period ${p.type}">
        <button class="body" data-action="edit" data-index="${i}" ${locked ? "disabled" : ""}>
          <span class="kind">${esc(kind(p.type))}</span><b>${esc(p.start)}–${esc(p.end)}</b>${caps.power ? `<span class="pw">${esc(Number(p.power).toFixed(1))} kW</span>` : ""}</button>
        <button class="del" data-action="delete" data-index="${i}" ${locked ? "disabled" : ""} aria-label="${esc(t.touDelete)}">✕</button></div>`).join("");
    const f = this.form;
    const form = f ? `<div class="form">
        <div class="types"><button data-action="type" data-type="charge" aria-pressed="${f.type === "charge"}">${esc(t.charge)}</button>
          <button data-action="type" data-type="discharge" aria-pressed="${f.type === "discharge"}">${esc(t.discharge)}</button></div>
        <div class="fields"><label>${esc(t.touStart)}<input type="time" step="60" data-field="start" value="${esc(f.start)}"></label>
          <label>${esc(t.touEnd)}<input type="time" step="60" data-field="end" value="${esc(f.end)}"></label>
          ${caps.power ? `<label class="wide">${esc(t.touPower)}<span class="kw"><input type="number" min="0" step="0.1" ${Number.isFinite(this.maxPower) ? `max="${this.maxPower}"` : ""}
            inputmode="decimal" data-field="power" value="${esc(f.power)}"><small>kW</small></span></label>` : ""}</div>
        <div class="err" ${this.formError ? "" : "hidden"}>${esc((t[this.formError] || "").replace("{max}", this.maxPower).replaceAll("{count}", caps.perType))}</div>
        <div class="row-actions"><button class="ok" data-action="ok">${esc(t.touOk)}</button><button class="plain" data-action="close">${esc(t.cancel)}</button></div>
      </div>` : "";
    const days = TOU_DAYS.map((d) => `<button class="day" data-day="${d}" aria-pressed="${plan.weekdays.includes(d)}" ${locked ? "disabled" : ""}>${esc((t.weekdays || {})[d] || d)}</button>`).join("");
    const outside = plan.outside_mode !== undefined ? `<label class="outside">${esc(t.touOutside)}
        <select data-outside ${locked ? "disabled" : ""}>${TOU_OUTSIDE.map((m) => `<option value="${m}" ${m === plan.outside_mode ? "selected" : ""}>${esc(this.modeWord(m))}</option>`).join("")}</select></label>` : "";
    const note = offline ? "" : remote ? t.touRemote : "";
    // While the plan is not in use (another EMS mode) only the title line shows; a
    // tap opens it. Never folded with unsaved edits.
    const foldable = sensor?.state === "inactive";
    const folded = foldable && !this.open && !this.draft && !this.form;
    const summary = plan.periods.length ? ` · ${plan.periods.length}` : "";
    this.root.innerHTML = `<style>
      :host{display:block} ha-card{padding:16px}
      .head{display:flex;align-items:center;gap:8px;margin-bottom:10px;width:100%;padding:0;border:0;background:none;font:inherit;
        color:var(--primary-text-color);text-align:left} .head h3{margin:0;font-size:16px;font-weight:600}
      button.head{cursor:pointer} .folded .head{margin-bottom:0}
      .head .now{margin-left:auto;font-size:13px;color:var(--secondary-text-color)}
      .chev{--mdc-icon-size:20px;color:var(--secondary-text-color);transition:transform .2s} .open .chev{transform:rotate(180deg)}
      .period{display:flex;align-items:center;gap:6px;margin-bottom:6px}
      .body{flex:1;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;border:0;cursor:pointer;font:inherit;text-align:left;
        color:var(--primary-text-color);background:var(--secondary-background-color)}
      .body b{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums} .body .pw{margin-left:auto;color:var(--secondary-text-color);font-size:13px}
      .kind{font-size:12px;padding:2px 8px;border-radius:10px;color:#fff;background:#38d39f} .discharge .kind{background:#ff9f43}
      .del{width:32px;height:32px;border-radius:50%;border:0;cursor:pointer;color:var(--secondary-text-color);background:none;font-size:14px}
      .empty{font-size:13px;color:var(--secondary-text-color);padding:6px 2px 10px}
      .add{width:100%;min-height:40px;border-radius:10px;border:1px dashed var(--divider-color);background:none;cursor:pointer;font:inherit;color:var(--primary-color)}
      .form{border:1px solid var(--divider-color);border-radius:12px;padding:12px;margin:4px 0 8px}
      .types{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
      .types button,.day{min-height:34px;border-radius:9px;border:1px solid var(--divider-color);cursor:pointer;font:inherit;font-size:13px;
        color:var(--primary-text-color);background:var(--secondary-background-color)}
      [aria-pressed="true"]{background:var(--primary-color)!important;color:var(--text-primary-color,#fff)!important;border-color:var(--primary-color)!important}
      /* Start and end side by side, power on its own row: three in a row overlap on a
         phone (a time input has a minimum width). 16 px text: iOS zooms the page into
         smaller inputs. */
      .fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px} .fields .wide{grid-column:1/-1}
      label{font-size:12px;color:var(--secondary-text-color);display:flex;flex-direction:column;gap:4px;min-width:0}
      input,select{font:inherit;font-size:16px;min-height:42px;box-sizing:border-box;width:100%;min-width:0;border-radius:10px;padding:6px 10px;
        border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
      input[type=time]{appearance:none;-webkit-appearance:none}
      .kw{display:flex;align-items:center;gap:4px}
      .err{color:var(--error-color,#d32f2f);font-size:13px;margin-top:8px}
      .row-actions,.actions{display:flex;gap:8px;margin-top:10px} .actions{margin-top:14px}
      .ok,.save{flex:1;min-height:38px;border:0;border-radius:10px;font:inherit;font-weight:600;cursor:pointer;color:var(--text-primary-color,#fff);background:var(--primary-color)}
      .plain{min-height:38px;padding:0 14px;border-radius:10px;font:inherit;cursor:pointer;color:var(--primary-text-color);background:none;border:1px solid var(--divider-color)}
      h4{margin:14px 0 6px;font-size:13px;font-weight:500;color:var(--secondary-text-color)}
      .days{display:grid;grid-template-columns:repeat(7,1fr);gap:4px} .day{min-height:30px;padding:0}
      .outside{margin-top:12px}
      button:disabled{opacity:.5;cursor:default}
      .msg{font-size:13px;margin-top:8px;color:var(--secondary-text-color)} .msg.err{color:var(--error-color,#d32f2f)}
      [hidden]{display:none!important}
    </style><ha-card class="${folded ? "folded" : foldable ? "open" : ""}">
      <${foldable ? `button type="button" data-action="fold" aria-expanded="${!folded}"` : "div"} class="head"><ha-icon icon="mdi:calendar-clock"></ha-icon><h3>${esc(t.tou)}</h3>
        <span class="now">${sensor && hass.formatEntityState ? esc(hass.formatEntityState(sensor)) : ""}${esc(summary)}</span>
        ${foldable ? `<ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>` : ""}</${foldable ? "button" : "div"}>
      ${folded ? "" : `${rows || `<div class="empty">${esc(t.noPeriods)}</div>`}
      ${form || `<button class="add" data-action="add" ${locked ? "disabled" : ""}>+ ${esc(t.touAdd)}</button>`}
      ${caps.days ? `<h4>${esc(t.activeDays)}</h4><div class="days">${days}</div>` : ""}
      ${outside}
      <div class="actions" ${this.dirty ? "" : "hidden"}>
        <button class="save" data-action="save" ${this.busy || this.form ? "disabled" : ""}>${esc(this.busy ? t.applying : t.touSave)}</button>
        <button class="plain" data-action="cancel" ${this.busy ? "disabled" : ""}>${esc(t.touUndo)}</button></div>
      <div class="msg${this.failed ? " err" : ""}" role="status" ${this.message || note ? "" : "hidden"}>${esc(this.message || note)}</div>`}
    </ha-card>`;
  }

  getCardSize() { return 6; }
  getGridOptions() { return { columns: 12 }; }
}

// On a phone the three desktop columns become three pages under one switch bar
// (user decision 2026-11-19): the same cards, one topic at a time. The cards are
// HA's own (made with its card helpers) laid out on the same 12-column grid as a
// section, so half-width tiles still sit side by side. The page shown last is
// remembered in this browser.
const TABS_KEY = "energylib.mobileTab";

class EnergyLibTabsCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.pages = null;
    let saved = 0;
    try { saved = Number(localStorage.getItem(TABS_KEY)) || 0; } catch (e) { /* no storage */ }
    this.index = Math.min(Math.max(saved, 0), (config.tabs || []).length - 1);
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
    root.innerHTML = `<style>
      :host{display:block;max-width:760px;margin:0 auto}
      /* Bottom navigation like a phone app (user decision 2026-11-23): the top of the
         screen goes to the house. Fixed to the screen bottom, across this card's width
         (set from script, so HA's sidebar on a tablet stays clear). */
      .bar{position:fixed;left:var(--bar-left,0);width:var(--bar-width,100%);bottom:0;z-index:5;display:grid;box-sizing:border-box;
        grid-template-columns:repeat(${(config.tabs || []).length || 1},1fr);gap:4px;padding:4px 8px calc(4px + env(safe-area-inset-bottom,0px));
        background:color-mix(in srgb,var(--card-background-color,#1c1c1c) 94%,transparent);backdrop-filter:blur(8px);
        border-top:1px solid var(--divider-color,#333)}
      .tab{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:48px;border:0;border-radius:10px;cursor:pointer;
        font:inherit;font-size:12px;font-weight:500;color:var(--secondary-text-color);background:none}
      .tab ha-icon{--mdc-icon-size:22px}
      .tab[aria-selected="true"]{color:var(--primary-color)}
      .page{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:8px;
        padding-bottom:calc(72px + env(safe-area-inset-bottom,0px))}
      .bar[hidden]{display:none}
      .cell{grid-column:span 12;min-width:0}
      .cell.half{grid-column:span 6}
    </style>
    <div class="bar" role="tablist">${(config.tabs || []).map((tab, i) => `<button class="tab" role="tab" data-i="${i}"
      aria-selected="${i === this.index}"><ha-icon icon="${esc(tab.icon)}"></ha-icon>${esc(tab.title)}</button>`).join("")}</div>
    <div class="page" id="page" role="tabpanel"></div>`;
    root.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => this.show(Number(b.dataset.i))));
    // A single page needs no navigation.
    root.querySelector(".bar").hidden = (config.tabs || []).length < 2;
    if (root.querySelector(".bar").hidden) root.getElementById("page").style.paddingBottom = "0";
  }

  // The fixed bar spans the page column, not HA's sidebar.
  place() {
    const rect = this.getBoundingClientRect();
    if (!rect.width) return;
    this.style.setProperty("--bar-left", `${rect.left}px`);
    this.style.setProperty("--bar-width", `${rect.width}px`);
  }

  connectedCallback() {
    this.resizer = this.resizer || new ResizeObserver(() => this.place());
    this.resizer.observe(this);
    this.onResize = this.onResize || (() => this.place());
    window.addEventListener("resize", this.onResize);
    this.place();
  }

  disconnectedCallback() {
    this.resizer?.disconnect();
    window.removeEventListener("resize", this.onResize);
  }

  async build() {
    const helpers = await window.loadCardHelpers();
    // A card asking to be rebuilt (its type loaded late) is replaced in place, and
    // the new one can ask again.
    const make = (card) => {
      const el = helpers.createCardElement(card);
      el.addEventListener("ll-rebuild", (e) => {
        e.stopPropagation();
        const fresh = make(card);
        fresh.hass = this._hass;
        el.replaceWith(fresh);
      });
      return el;
    };
    this.pages = (this.config.tabs || []).map((tab) => tab.cards.map((card) => {
      const cell = document.createElement("div");
      cell.className = Number(card.grid_options?.columns) === 6 ? "cell half" : "cell";
      cell.append(make(card));
      return { cell };
    }));
    this.show(this.index);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.started) {
      this.started = true;
      this.build();
      return;
    }
    // Only the page on screen gets updates; the others catch up when shown.
    for (const { cell } of this.pages?.[this.index] || []) cell.firstElementChild.hass = hass;
  }

  show(index) {
    this.index = index;
    try { localStorage.setItem(TABS_KEY, String(index)); } catch (e) { /* no storage */ }
    this.shadowRoot.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(Number(b.dataset.i) === index)));
    if (!this.pages) return;
    const page = this.pages[index] || [];
    for (const { cell } of page) cell.firstElementChild.hass = this._hass;
    this.shadowRoot.getElementById("page").replaceChildren(...page.map((p) => p.cell));
    window.scrollTo?.({ top: 0 });
  }

  getCardSize() { return 12; }
  getGridOptions() { return { columns: "full" }; }
}

// A few rows that open another page of Home Assistant (in place of big buttons).
class EnergyLibLinksCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
    root.innerHTML = `<style>
      :host{display:block} ha-card{padding:4px 0}
      a{display:flex;align-items:center;gap:12px;padding:10px 16px;color:var(--primary-text-color);text-decoration:none;font-size:14px}
      a:hover{background:color-mix(in srgb,var(--primary-text-color) 6%,transparent)}
      a ha-icon{color:var(--secondary-text-color)} a span{flex:1} a .go{--mdc-icon-size:18px}
    </style><ha-card>${(config.links || []).map((l) => `<a href="${esc(l.path)}"><ha-icon icon="${esc(l.icon)}"></ha-icon>`
      + `<span>${esc(l.name)}</span><ha-icon class="go" icon="mdi:chevron-right"></ha-icon></a>`).join("")}</ha-card>`;
    root.querySelectorAll?.("a").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();
      // HA's own in-app navigation.
      history.pushState(null, "", a.getAttribute("href"));
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }));
  }
  set hass(hass) { this._hass = hass; }
  getCardSize() { return (this.config?.links || []).length; }
  getGridOptions() { return { columns: 12 }; }
}

// One everyday operation card. Native entities remain available for automation;
// users do not need to assemble them or visit the entity registry.
class EnergyLibDispatchCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.direction = "charge";
    this.queue = Promise.resolve();
    this.root = this.shadowRoot || this.attachShadow({ mode: "open" });
    this.root.innerHTML = `
      <style>
        :host{display:block}ha-card{padding:20px;color:var(--primary-text-color)}
        h3{margin:0 0 16px;font-size:20px}p{margin:10px 0;color:var(--secondary-text-color);line-height:1.5}
        .direction,.fields,.actions{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
        button,input{font:inherit;box-sizing:border-box;border-radius:10px;min-height:48px}
        button{cursor:pointer;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);padding:10px 14px}
        button[aria-pressed="true"]{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color)}
        button:disabled{opacity:.45;cursor:default}label{font-size:14px;display:block}
        .input{display:flex;align-items:center;gap:8px;margin-top:8px}input{width:100%;min-width:0;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);padding:10px}
        .unit{min-width:32px;color:var(--secondary-text-color)}
        .actions{grid-template-columns:1fr;margin-bottom:0}.actions.running{grid-template-columns:1fr 1fr}
        #start{background:var(--primary-color);color:var(--text-primary-color,#fff);border:0;font-weight:600}
        #stop{color:var(--error-color,#d32f2f);border-color:var(--error-color,#d32f2f);font-weight:600}
        .result{border-radius:10px;padding:14px;background:var(--secondary-background-color);margin-bottom:16px;line-height:1.7}
        #remaining{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
        #error{color:var(--error-color,#d32f2f)}small{color:var(--secondary-text-color)}
        .access{display:flex;align-items:center;justify-content:space-between;gap:12px}
        #access{display:inline-flex;align-items:center;gap:8px;min-height:32px;padding:4px 12px 4px 4px;border-radius:16px;flex:none}
        #access::before{content:"";width:24px;height:24px;border-radius:50%;background:var(--disabled-color,#888)}
        #access[aria-checked="true"]{background:color-mix(in srgb,var(--primary-color) 18%,var(--secondary-background-color));border-color:var(--primary-color)}
        #access[aria-checked="true"]::before{background:var(--primary-color)}
        [hidden]{display:none!important}
      </style>
      <ha-card>
        <h3 id="title"></h3>
        <div class="access"><span id="access-label"></span> <button id="access" type="button" role="switch" aria-checked="false"></button></div>
        <p id="access-note"></p>
        <div id="operation" hidden>
        <div class="direction" role="group" id="direction">
          <button id="charge" type="button" aria-pressed="true"></button>
          <button id="discharge" type="button" aria-pressed="false"></button>
        </div>
        <div class="fields">
          <label><span id="power-label"></span><div class="input"><input id="power" type="number" min="0" step="0.1" inputmode="decimal" required><span class="unit">kW</span></div></label>
          <label><span id="duration-label"></span><div class="input"><input id="duration" type="number" min="1" max="1440" step="1" inputmode="numeric" required><span class="unit" id="minutes"></span></div></label>
        </div>
        <div class="result" aria-live="polite"><strong id="status"></strong><div id="remaining"></div><div id="target"></div><div id="actual"></div><small id="end"></small></div>
        <p id="note"></p><p id="warning" role="status"></p><button id="leave-tou" type="button" hidden></button><p id="error" role="alert" hidden></p>
        <div class="actions" id="actions"><button id="start" type="button"></button><button id="stop" type="button" hidden></button></div>
        </div>
        <p id="access-error" role="alert" hidden></p>
      </ha-card>`;
    this.el = (id) => this.root.getElementById(id);
    ["charge", "discharge"].forEach((direction) => {
      this.el(direction).onclick = () => { this.direction = direction; this.render(); };
    });
    ["power", "duration"].forEach((id) => {
      this.el(id).oninput = () => {
        this.dirty = true;
        clearTimeout(this.saveTimer);
        this.saveTimer = setTimeout(() => this.saveOnly(), 600);
        this.render();
      };
    });
    this.el("start").onclick = () => this.start();
    this.el("stop").onclick = () => this.stop();
    this.el("leave-tou").onclick = () => this.leaveTou();
    this.el("access").onclick = () => this.toggleAccess();
  }
  set hass(hass) { this._hass = hass; this.render(); }
  connectedCallback() { if (!this.timer) this.timer = setInterval(() => { if (onScreen(this)) this.render(); }, 1000); }
  disconnectedCallback() { clearInterval(this.timer); clearTimeout(this.saveTimer); this.timer = null; }
  getCardSize() { return 6; }
  get texts() {
    return translate(this._hass, "dispatch");
  }
  snapshot(allowZero = false) {
    const power = Number(this.el("power").value), minutes = Number(this.el("duration").value);
    const limit = Number(this.settings.max_power_kw);
    if (!Number.isFinite(power) || !Number.isFinite(minutes) || power < 0 || (!allowZero && power === 0) || !Number.isInteger(minutes) || minutes < 1 || minutes > 1440) throw new Error(this.texts.invalid);
    if (!Number.isFinite(limit) || limit <= 0 || power > limit) throw new Error(`${this.texts.powerLimit}${Number.isFinite(limit) ? ` (${limit} kW)` : ""}`);
    return { power, minutes, direction: this.direction };
  }
  async saveValues(snapshot) {
    await this._hass.callService("energylib", "set_remote_parameters", {
      device_id: this.config.device_id, power_kw: snapshot.power, duration_minutes: snapshot.minutes,
    });
  }
  get settings() {
    const states = this._hass.states;
    return states[this.config.status_entity]?.attributes.settings || {
      power_kw: states[this.config.power_entity]?.state,
      duration_minutes: states[this.config.duration_entity]?.state,
      max_power_kw: states[this.config.power_entity]?.attributes.max,
    };
  }
  showError(error) { this.el("error").textContent = error.message || String(error); this.el("error").hidden = false; }
  saveOnly() {
    if (this.busy || !this.accessEnabled) return;
    let snapshot;
    try { snapshot = this.snapshot(true); } catch (error) { this.showError(error); return; }
    this.queue = this.queue.catch(() => {}).then(() => this.saveValues(snapshot)).catch((error) => this.showError(error));
  }
  async start() {
    if (this.busy || this.controlsDisabled || !this.accessEnabled || this._hass.states[this.config.status_entity]?.attributes.entities_ready === false) return;
    let snapshot;
    try { snapshot = this.snapshot(); } catch (error) { this.showError(error); return; }
    clearTimeout(this.saveTimer); this.busy = true; this.el("error").hidden = true; this.render();
    try {
      this.queue = this.queue.catch(() => {}).then(async () => {
        await this.saveValues(snapshot);
        // Explicit signed power/duration make this click independent of another
        // client's later draft edits. The backend still gates/serializes/readbacks.
        await this._hass.callService("energylib", "set_battery_power", {
          device_id: this.config.device_id,
          power_kw: snapshot.direction === "charge" ? -snapshot.power : snapshot.power,
          duration: snapshot.minutes * 60,
        });
      });
      await this.queue;
    } catch (error) { this.showError(error); }
    finally { this.busy = false; this.render(); }
  }
  async stop() {
    if (this.busy) return;
    clearTimeout(this.saveTimer); this.busy = true; this.el("error").hidden = true; this.render();
    try {
      this.queue = this.queue.catch(() => {}).then(() => this._hass.callService("energylib", "stop_battery_power", { device_id: this.config.device_id }));
      await this.queue;
    } catch (error) { this.showError(error); }
    finally { this.busy = false; this.render(); }
  }
  async leaveTou() {
    if (this.busy || !this.accessEnabled || !this.config.ems_entity) return;
    this.busy = true; this.el("error").hidden = true; this.render();
    try {
      await this._hass.callService("energylib", "set_ems_mode", { device_id: this.config.device_id, mode: "self_consumption" });
    } catch (error) { this.showError(error); }
    finally { this.busy = false; this.render(); }
  }
  get accessState() {
    const value = this._hass?.states[this.config.status_entity]?.attributes.remote_access_enabled;
    return typeof value === "boolean" ? value : null;
  }
  get accessEnabled() { return this.accessState === true; }
  get controlsDisabled() {
    return this._hass?.states[this.config.control_status_entity]?.attributes.control_access_enabled === false;
  }
  async toggleAccess() {
    if (this.busy || this.controlsDisabled || this.accessState === null) return;
    this.busy = true; clearTimeout(this.saveTimer); this.el("access-error").hidden = true; this.render();
    try {
      await this.queue.catch(() => {});
      await this._hass.callService("energylib", "set_remote_access", { device_id: this.config.device_id, enabled: !this.accessEnabled });
    } catch (error) {
      this.el("access-error").textContent = error.message || String(error); this.el("access-error").hidden = false;
    } finally { this.busy = false; this.render(); }
  }
  render() {
    if (!this.config || !this._hass || !this.root) return;
    const t = this.texts, states = this._hass.states;
    const state = states[this.config.status_entity], attrs = state?.attributes || {};
    const status = state?.state || "unknown";
    const accessKnown = this.accessState !== null && state && !["unknown", "unavailable"].includes(state.state);
    const syncing = this.accessEnabled && attrs.entities_ready === false;
    this.el("operation").hidden = this.controlsDisabled || !this.accessEnabled;
    this.el("access-label").textContent = t.access;
    this.el("access").textContent = this.busy ? t.busy : this.accessEnabled ? t.disable : t.enable;
    this.el("access").setAttribute("aria-checked", String(this.accessEnabled));
    this.el("access").setAttribute("aria-label", t.access);
    this.el("access").disabled = this.busy || this.controlsDisabled || !accessKnown;
    this.el("access-note").textContent = this.controlsDisabled ? t.controlsOff : !accessKnown ? t.unavailable : syncing ? t.syncing : this.accessEnabled ? t.onNote : t.offNote;
    const running = ["charging", "discharging", "holding"].includes(status);
    const stoppable = running || status === "unconfirmed";
    const settings = this.settings;
    const available = state && !["unknown", "unavailable"].includes(state.state)
      && [settings.power_kw, settings.duration_minutes, settings.max_power_kw].every((v) => v !== undefined && v !== null && v !== "" && Number.isFinite(Number(v)));
    if (!this.initialized && available) {
      this.el("power").value = settings.power_kw; this.el("duration").value = settings.duration_minutes; this.initialized = true;
    }
    this.el("power").max = settings.max_power_kw ?? "";
    const text = (id, value) => { this.el(id).textContent = value; };
    text("title", t.title); text("charge", t.charge); text("discharge", t.discharge);
    this.el("direction").setAttribute("aria-label", t.title);
    this.el("charge").setAttribute("aria-pressed", String(this.direction === "charge"));
    this.el("discharge").setAttribute("aria-pressed", String(this.direction === "discharge"));
    text("power-label", t.power); text("duration-label", t.duration); text("minutes", t.minutes);
    this.el("power").setAttribute("aria-label", `${t.power} kW`); this.el("duration").setAttribute("aria-label", `${t.duration} ${t.minutes}`);
    text("status", t[status] || t.unknown);
    text("note", running ? t.runningNote : t.note);
    const tou = states[this.config.work_mode_entity]?.state === "time_of_use";
    text("warning", !available ? t.unavailable : tou ? t.tou : "");
    this.el("leave-tou").hidden = !tou || !this.config.ems_entity;
    this.el("leave-tou").disabled = this.busy; text("leave-tou", t.leaveTou);
    let valid = false; try { this.snapshot(); valid = true; } catch { /* inline constraints; no command */ }
    this.el("start").disabled = this.busy || this.controlsDisabled || !this.accessEnabled || syncing || !available || !valid || tou;
    text("start", this.busy ? t.busy : running ? t.update : this.direction === "charge" ? t.startCharge : t.startDischarge);
    this.el("stop").disabled = this.busy; this.el("stop").hidden = !stoppable; text("stop", t.stop);
    this.el("actions").classList.toggle("running", stoppable);
    const formatPower = (value) => typeof value === "number" && Number.isFinite(value) ? `${Math.abs(value).toFixed(2)} kW` : "—";
    text("target", attrs.target_power_kw == null ? "" : `${t.target}: ${t[attrs.target_power_kw < 0 ? "charge" : "discharge"]} ${formatPower(attrs.target_power_kw)}`);
    const actualDirection = attrs.actual_power_kw < 0 ? t.charge : attrs.actual_power_kw > 0 ? t.discharge : "";
    text("actual", `${t.actual}: ${actualDirection} ${formatPower(attrs.actual_power_kw)}`);
    const end = attrs.end_time ? Date.parse(attrs.end_time) : NaN;
    const remaining = Number.isFinite(end) ? Math.max(0, Math.ceil((end - Date.now()) / 1000)) : 0;
    text("remaining", running && Number.isFinite(end) ? remaining ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}` : t.pending : "");
    text("end", `${t.until}: ${Number.isFinite(end) && stoppable ? new Date(end).toLocaleString() : stoppable ? t.noEnd : ["idle", "stopped", "completed"].includes(status) ? t.noTask : t.unknown}`);
  }
}
