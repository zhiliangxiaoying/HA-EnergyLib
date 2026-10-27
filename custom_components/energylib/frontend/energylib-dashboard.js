// EnergyLIB dashboard strategy: builds the EnergyLIB page from the user's own devices.
// Served by the integration at /energylib_static/; users add it from
// Settings > Dashboards > Add dashboard > EnergyLIB, or with
//   strategy:
//     type: custom:energylib
// Entities are found by device and translation key, never by entity ID, so
// renamed entities keep working. Controls appear only when they are enabled.

// The integration loads this file with ?v=<hash of this file and the twin files>;
// the twin page reuses it so browsers fetch new twin files after an update.
const VERSION = new URL(import.meta.url).searchParams.get("v") || "dev";
const TWIN_URL = `/energylib_twin/index.html?v=${encodeURIComponent(VERSION)}`;

const TEXT = {
  en: {
    twin: "Digital twin",
    overview: "Overview",
    flow: "Live energy flow",
    flowTitle: "Power flow",
    flowMissing: "Some power values are unavailable; the flow appears again once the device reports.",
    negativeLoad: "Home load reads {value}; shown as 0 W, likely a metering error. Check the wiring if it persists.",
    battery: "Battery",
    soc: "State of charge",
    todayCharge: "Charged today",
    todayDischarge: "Discharged today",
    socTrend: "State of charge, 24 h",
    signs: "Battery **+ = discharging, − = charging**; grid **+ = importing, − = exporting**.",
    today: "Today",
    todaySolar: "Solar",
    todayLoad: "Home",
    todayImport: "Grid import",
    todayExport: "Grid export",
    week: "Daily energy, last 7 days",
    powerTrend: "Power, 24 h",
    status: "Status",
    alarms: "Faults and warnings",
    controls: "Controls",
    remote: "Remote dispatch",
    startCharge: "Start charging",
    startDischarge: "Start discharging",
    stopRemote: "Stop immediately",
    targetPower: "Battery power",
    duration: "Duration (minutes)",
    remoteNote:
      "Enter a **positive power** and duration in **minutes**, then press Start. Editing the draft does not change the device or a running task. TOU must be exited first. Enable the new entities on the device page. Heartbeats run internally; the current timeout is 60 s with self-consumption as the fallback (firmware validation pending).",
    controlsOff:
      "Controls are off. Enable **Allow control** in the integration options to change EMS mode, SOC limits and remote dispatch.",
    tou: "Time-of-use schedule",
    touEdit: "Edit schedule",
    deviceInfo: "Device",
    totals: "Lifetime energy",
    log: "Activity",
    noDevice: "No EnergyLIB device found. Add the EnergyLIB integration first.",
    solar: "Solar",
    load: "Home",
    batteryShort: "Battery",
    grid: "Grid",
    importing: "Importing",
    exporting: "Exporting",
    charging: "Charging",
    discharging: "Discharging",
    gridMissing: "Grid offline",
    batteryMissing: "No battery",
    offGrid: "Off-grid",
    weekdays: { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" },
    charge: "Charge",
    discharge: "Discharge",
    noPeriods: "No periods.",
    activeDays: "Active",
  },
  zh: {
    twin: "数字孪生",
    overview: "总览",
    flow: "实时能流",
    flowTitle: "实时功率流向",
    flowMissing: "部分功率数据暂不可用，恢复连接后自动显示流向。",
    negativeLoad: "家庭负载读数为 {value}，已按 0 W 显示，可能是计量误差；持续出现时请核对接线。",
    battery: "储能电池",
    soc: "电池电量",
    todayCharge: "今日充电",
    todayDischarge: "今日放电",
    socTrend: "24 小时电量",
    signs: "电池 **正为放电、负为充电**；电网 **正为买电、负为卖电**。",
    today: "今日电量",
    todaySolar: "光伏发电",
    todayLoad: "家庭用电",
    todayImport: "电网购电",
    todayExport: "上网电量",
    week: "近 7 天每日电量",
    powerTrend: "24 小时功率趋势",
    status: "运行状态",
    alarms: "故障与告警",
    controls: "运行控制",
    remote: "远程调度",
    startCharge: "开始充电",
    startDischarge: "开始放电",
    stopRemote: "立即停止",
    targetPower: "充放电功率",
    duration: "充放电时长（分钟）",
    remoteNote:
      "输入**正数功率**和持续时间（**分钟**），再点击开始。修改草稿不会写设备，也不会改变运行中的任务；需要先退出定时充放电模式。在设备页启用新实体后可操作。心跳由后台维护；当前超时 60 秒、退出回到自发自用，固件行为待实机确认。",
    controlsOff: "控制未开启。在集成选项中打开 **允许控制** 后，可以调整EMS模式、SOC 和远程调度。",
    tou: "定时充放电",
    touEdit: "编辑定时计划",
    deviceInfo: "设备信息",
    totals: "累计电量",
    log: "运行记录",
    noDevice: "没有找到 EnergyLIB 设备，请先添加 EnergyLIB 集成。",
    solar: "光伏",
    load: "家庭负载",
    batteryShort: "储能电池",
    grid: "电网",
    importing: "买电",
    exporting: "卖电",
    charging: "充电",
    discharging: "放电",
    gridMissing: "电网断开",
    batteryMissing: "电池未接入",
    offGrid: "离网运行",
    weekdays: { mon: "周一", tue: "周二", wed: "周三", thu: "周四", fri: "周五", sat: "周六", sun: "周日" },
    charge: "充电",
    discharge: "放电",
    noPeriods: "暂无时段。",
    activeDays: "生效",
  },
};

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

function buildView(hass, device, t, multiple) {
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
  const power = ["solar_power", "load_power", "battery_power", "grid_power"].map(s);

  // Live flow: the built-in card. It shows a notice while a power value is missing.
  const flowCards = [heading(t.flow, "mdi:transit-connection-variant")];
  if (has(...power)) {
    const [solar, load, battery, grid] = power;
    flowCards.push({
      type: "custom:energylib-flow",
      title: t.flowTitle,
      solar_entity: solar,
      load_entity: load,
      battery_entity: battery,
      grid_entity: grid,
      soc_entity: s("battery_total_soc"),
      running_entity: s("running_state"),
      alarm_entity: s("alarm_status"),
      text: {
        solar: t.solar, grid: t.grid, load: t.load, batteryShort: t.batteryShort,
        importing: t.importing, exporting: t.exporting, charging: t.charging, discharging: t.discharging,
        gridMissing: t.gridMissing, batteryMissing: t.batteryMissing, offGrid: t.offGrid,
        negativeLoad: t.negativeLoad, unavailable: t.flowMissing,
      },
    });
  }
  flowCards.push(
    ...compact([
      tile(power[0], "mdi:solar-power", "amber", { grid_options: { columns: 6 } }),
      tile(power[1], "mdi:home-lightning-bolt", "blue", { grid_options: { columns: 6 } }),
      tile(power[2], "mdi:battery-charging", "green", { grid_options: { columns: 6 } }),
      tile(power[3], "mdi:transmission-tower", "purple", { grid_options: { columns: 6 } }),
    ])
  );

  const batteryCards = compact([
    heading(t.battery, "mdi:home-battery"),
    s("battery_total_soc") && {
      type: "gauge",
      entity: s("battery_total_soc"),
      name: t.soc,
      min: 0,
      max: 100,
      needle: true,
      severity: { red: 0, yellow: 20, green: 40 },
    },
    stat(s("battery_charge_energy"), t.todayCharge, "mdi:battery-arrow-up"),
    stat(s("battery_discharge_energy"), t.todayDischarge, "mdi:battery-arrow-down"),
    s("battery_total_soc") && {
      type: "history-graph",
      title: t.socTrend,
      hours_to_show: 24,
      entities: [{ entity: s("battery_total_soc"), name: "SOC" }],
    },
    { type: "markdown", content: `<ha-icon icon="mdi:information-outline"></ha-icon> ${t.signs}` },
  ]);

  const controls = [
    ["select.work_mode", "mdi:cog-transfer", "indigo", [{ type: "select-options" }]],
    ["number.backup_soc", "mdi:battery-lock", "teal", [{ type: "numeric-input", style: "slider" }]],
    ["number.min_soc", "mdi:battery-low", "orange", [{ type: "numeric-input", style: "slider" }]],
    ["number.export_limit", "mdi:transmission-tower-export", "cyan", [{ type: "numeric-input", style: "slider" }]],
    ["switch.export_limit_enable", "mdi:transmission-tower-off", "cyan", [{ type: "toggle" }]],
    ["switch.grid_charge", "mdi:battery-charging-wireless", "purple", [{ type: "toggle" }]],
    // Advanced battery limits start disabled; they appear once enabled on the device page.
    ["number.max_soc", "mdi:battery-arrow-up", "green", [{ type: "numeric-input", style: "slider" }]],
    ["number.max_charge_current", "mdi:current-dc", "teal", [{ type: "numeric-input", style: "slider" }]],
    ["number.max_discharge_current", "mdi:current-dc", "orange", [{ type: "numeric-input", style: "slider" }]],
  ].flatMap(([key, icon, color, features]) => {
    const card = tile(ent[key], icon, color, { features });
    if (!card) return [];
    if (key !== "select.work_mode" || !s("work_mode")) return [card];
    // Read-only modes are not selectable options. Display the authoritative
    // sensor while one is active instead of an "unknown" native selector.
    const readOnlyModes = ["dynamic_tariff", "remote", "voltage_regulation"];
    return [
      { type: "conditional", conditions: [{ condition: "state", entity: s("work_mode"), state_not: readOnlyModes }], card },
      { type: "conditional", conditions: [{ condition: "state", entity: s("work_mode"), state: readOnlyModes }], card: tile(s("work_mode"), icon, color) },
    ];
  });
  const dispatch = s("remote_status") && {
    type: "custom:energylib-dispatch", device_id: device.id,
    power_entity: ent["number.remote_target_power"], duration_entity: ent["number.remote_duration"],
    status_entity: s("remote_status"),
    control_status_entity: s("work_mode"),
    work_mode_entity: ent["select.work_mode"] || s("work_mode"),
  };
  const statusCards = compact([
    heading(t.status, "mdi:information-outline"),
    tile(s("running_state"), "mdi:power-plug", "green"),
    s("alarm_status") && { type: "custom:energylib-alarms", entity: s("alarm_status") },
    controls.some(Boolean) ? heading(t.controls, "mdi:tune") : tile(s("work_mode"), "mdi:cog-transfer", "indigo"),
    ...controls,
  ]);

  const dailyCards = compact([
    heading(t.today, "mdi:calendar-today"),
    stat(s("solar_energy"), t.todaySolar, "mdi:solar-power"),
    stat(s("load_energy"), t.todayLoad, "mdi:home-lightning-bolt"),
    stat(s("grid_import_energy"), t.todayImport, "mdi:transmission-tower-import"),
    stat(s("grid_export_energy"), t.todayExport, "mdi:transmission-tower-export"),
  ]);
  const historyCards = compact([
    heading(t.totals, "mdi:chart-box", "subtitle"),
    ...[
      ["solar_energy", "mdi:solar-power", "amber"],
      ["load_energy", "mdi:home-lightning-bolt", "blue"],
      ["grid_import_energy", "mdi:transmission-tower-import", "purple"],
      ["grid_export_energy", "mdi:transmission-tower-export", "cyan"],
      ["battery_charge_energy", "mdi:battery-arrow-up", "green"],
      ["battery_discharge_energy", "mdi:battery-arrow-down", "teal"],
    ].map(([key, icon, color]) => tile(s(key), icon, color, { grid_options: { columns: 6 } })),
  ]);
  const chartCards = compact([
    {
      type: "statistics-graph",
      title: t.week,
      days_to_show: 7,
      period: "day",
      stat_types: ["change"],
      chart_type: "bar",
      grid_options: { columns: "full" },
      entities: compact([
        s("solar_energy") && { entity: s("solar_energy"), name: t.todaySolar },
        s("load_energy") && { entity: s("load_energy"), name: t.todayLoad },
        s("grid_import_energy") && { entity: s("grid_import_energy"), name: t.todayImport },
        s("grid_export_energy") && { entity: s("grid_export_energy"), name: t.todayExport },
      ]),
    },
    has(...power) && {
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
    },
  ]);

  const tou = s("tou_schedule");
  const wd = JSON.stringify(t.weekdays).replace(/"/g, "'");
  const touCards = compact([
    heading(t.tou, "mdi:calendar-clock"),
    tou && tile(tou, "mdi:calendar-clock", "indigo"),
    tou && {
      type: "markdown",
      content:
        // Offline or before the first read the sensor has no plan attributes;
        // .get() keeps HA's strict template rendering from raising errors.
        `{% set s = states['${tou}'] %}{% set wd = ${wd} %}` +
        `{% set periods = s.attributes.get('periods', []) if s else [] %}` +
        `{% if periods %}{% for p in periods %}- ` +
        `{{ '${t.charge}' if p.type == 'charge' else '${t.discharge}' }} {{ p.start }}–{{ p.end }}, {{ p.power }} kW\n` +
        `{% endfor %}\n${t.activeDays}: {% for d in s.attributes.get('weekdays', []) %}{{ wd.get(d, d) }}{{ ', ' if not loop.last }}{% endfor %}` +
        `{% else %}${t.noPeriods}{% endif %}`,
    },
    {
      type: "button",
      name: t.touEdit,
      icon: "mdi:calendar-edit",
      tap_action: { action: "navigate", navigation_path: "/config/integrations/integration/energylib" },
      grid_options: { columns: 6, rows: 2 },
    },
    {
      type: "button",
      name: t.deviceInfo,
      icon: "mdi:devices",
      tap_action: { action: "navigate", navigation_path: `/config/devices/device/${device.id}` },
      grid_options: { columns: 6, rows: 2 },
    },
  ]);

  // Read-only history stays with the measurements, away from the control column.
  const logCards = compact([
    heading(t.log, "mdi:history"),
    {
      type: "logbook",
      hours_to_show: 24,
      entities: compact([s("running_state"), s("alarm_status"), ent["select.work_mode"] || s("work_mode"), ent["switch.grid_charge"]]),
    },
  ]);

  const deviceName = device.name_by_user || device.name;
  return {
    // No view icons: HA then shows the tab titles as text.
    title: multiple ? deviceName : t.overview,
    path: multiple ? device.id : "overview",
    type: "sections",
    max_columns: 3,
    sections: [
      // Three continuous desktop columns: measurements on the left, every
      // writable control in the last column, with manual dispatch last.
      { type: "grid", cards: [...flowCards, ...dailyCards, ...historyCards, ...logCards] },
      { type: "grid", cards: [...batteryCards, ...chartCards] },
      { type: "grid", cards: [...statusCards, ...touCards, dispatch || { type: "markdown", content: t.controlsOff }] },
    ],
  };
}

// A standalone view uses the same content hash as the generated dashboard.
// Do not persist a versioned iframe URL in a user's dashboard configuration.
class EnergyLibTwinViewStrategy extends HTMLElement {
  static async generate() {
    return {
      type: "panel",
      cards: [{ type: "iframe", url: TWIN_URL, aspect_ratio: "56%" }],
    };
  }
}

class EnergyLibDashboardStrategy extends HTMLElement {
  static async generate(config, hass) {
    const t = (hass.locale?.language || hass.language || "en").startsWith("zh") ? TEXT.zh : TEXT.en;
    const devices = energyLibDevices(hass);
    if (!devices.length) {
      return {
        title: "EnergyLIB",
        views: [{ title: "EnergyLIB", cards: [{ type: "markdown", content: t.noDevice }] }],
      };
    }
    // Everyday overview first; the full-screen twin keeps its stable URL.
    const twin = {
      title: t.twin,
      path: "twin",
      ...await EnergyLibTwinViewStrategy.generate(),
    };
    return {
      title: "EnergyLIB",
      views: [...devices.map((device) => buildView(hass, device, t, devices.length > 1)), twin],
    };
  }
}

// Integration extra modules can run before the HA frontend installs its scoped
// custom-element registry; a definition made earlier is invisible to the
// frontend's lookup and the dashboard times out. Define once the app shell exists.
const STRATEGY_TAG = "ll-strategy-dashboard-energylib";
customElements.whenDefined("home-assistant").then(() => {
  if (!customElements.get(STRATEGY_TAG)) customElements.define(STRATEGY_TAG, EnergyLibDashboardStrategy);
  if (!customElements.get("ll-strategy-view-energylib-twin")) customElements.define("ll-strategy-view-energylib-twin", EnergyLibTwinViewStrategy);
  if (!customElements.get("energylib-alarms")) customElements.define("energylib-alarms", EnergyLibAlarmCard);
  if (!customElements.get("energylib-dispatch")) customElements.define("energylib-dispatch", EnergyLibDispatchCard);
  if (!customElements.get("energylib-remote-task")) customElements.define("energylib-remote-task", EnergyLibRemoteTaskCard);
  if (!customElements.get("energylib-flow")) customElements.define("energylib-flow", EnergyLibFlowCard);
});
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((s) => s.type === "energylib")) {
  window.customStrategies.push({
    type: "energylib",
    strategyType: "dashboard",
    name: "EnergyLIB",
    description: "3D digital twin, energy flow, battery, daily energy and controls for EnergyLIB inverters.",
    documentationURL: "https://github.com/zhiliangxiaoying/HA-EnergyLib",
  });
}

// Exposed for in-browser checks; not part of the HA strategy API.
window.energyLibDashboard = { buildView, deviceEntities, energyLibDevices, TEXT, flowModel, balanceWatts, flowSplit };

// Built-in live energy flow (no third-party card), laid out like HA's energy card:
// values inside the circles, one line per node-to-node flow with moving dots.
// Signs are the integration's: battery + = discharge, grid + = import.
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
      .notice{font-size:14px;padding:24px 0}
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
    this.$("stage").hidden = !m.ready;
    this.$("notice").hidden = m.ready;
    this.$("chip").hidden = !m.ready || !m.offGrid;
    this.$("chip").textContent = t.offGrid || "";
    if (!m.ready) {
      this.$("notice").textContent = t.unavailable || "";
      this.$("hint").hidden = true;
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
    const zh = lang.startsWith("zh");
    const attrs = state?.attributes || {};
    const active = attrs.alarms || [];
    const complete = attrs.complete === true && !["unknown", "unavailable"].includes(state?.state);
    const labels = zh ? {
      title: "设备告警",
      fault: "故障", warning: "警告", info: "提示", unknown: "未识别",
      normal: "正常", abnormal: "有异常", unrecognized: "未识别异常",
      partial: "数据不完整", offline: "通信不可用", stale: "上次有效状态，待刷新",
      current: "当前异常", history: "最近记录", empty: "暂无异常",
      first: "首次检测", last: "最近有效检测", cleared: "检测到恢复", pending: "未检测到恢复",
      note: "时间为 HA 观察时间；设备实际发生时间可能更早。",
    } : {
      title: "Device alerts",
      fault: "Fault", warning: "Warning", info: "Information", unknown: "Unrecognized",
      normal: "Normal", abnormal: "Abnormal", unrecognized: "Unrecognized alarm",
      partial: "Incomplete data", offline: "Communication unavailable", stale: "Last valid state, awaiting refresh",
      current: "Active alarms", history: "Recent occurrences (up to 100)", empty: "No records",
      first: "First observed", last: "Last valid observation", cleared: "Recovery observed", pending: "Recovery not observed",
      note: "Times are HA observations, not the device event time.",
    };
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
    add(body, "strong", complete && !active.length ? (zh ? "运行正常，无告警" : "No active alerts") : status);
    const time = (value) => value ? new Date(value).toLocaleString(zh ? "zh-CN" : "en") : "—";
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
    if (active.length) add(body, "h4", labels.current);
    active.forEach((alarm) => item(body, alarm));
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
    this.timer = setInterval(() => this.render(), 1000);
    this.render();
  }
  disconnectedCallback() { clearInterval(this.timer); this.timer = null; }
  render() {
    if (!this.config || !this._hass) return;
    const zh = (this._hass.locale?.language || this._hass.language || "en").startsWith("zh");
    const state = this._hass.states[this.config.entity];
    const attrs = state?.attributes || {};
    const status = state?.state || "unavailable";
    const names = zh ? {
      idle: "空闲", charging: "充电中", discharging: "放电中", holding: "零功率保持",
      stopped: "已停止", completed: "已到期", unconfirmed: "执行未确认", unavailable: "通信不可用", unknown: "未知",
    } : {
      idle: "Idle", charging: "Charging", discharging: "Discharging", holding: "Holding at zero power",
      stopped: "Stopped", completed: "Completed", unconfirmed: "Execution unconfirmed", unavailable: "Communication unavailable", unknown: "Unknown",
    };
    const card = document.createElement("ha-card");
    const body = document.createElement("div"); body.style.cssText = "padding:16px;line-height:1.8";
    const add = (tag, text) => { const el = document.createElement(tag); el.textContent = text; body.append(el); };
    add("strong", names[status] || status);
    const power = (value) => typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)} kW` : "—";
    add("div", `${zh ? "任务目标（负充正放）" : "Task target (− charge, + discharge)"}: ${power(attrs.target_power_kw)}`);
    add("div", `${zh ? "实际电池功率" : "Actual battery power"}: ${power(attrs.actual_power_kw)}`);
    if (attrs.end_time) {
      const end = Date.parse(attrs.end_time);
      if (Number.isFinite(end)) {
        add("div", `${zh ? "结束时间" : "Ends at"}: ${new Date(end).toLocaleString(zh ? "zh-CN" : "en")}`);
        const seconds = Math.max(0, Math.ceil((end - Date.now()) / 1000));
        add("div", seconds > 0
          ? `${zh ? "剩余" : "Remaining"}: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
          : (zh ? "等待后台确认退出" : "Waiting for backend exit confirmation"));
      }
    }
    card.append(body); this.replaceChildren(card);
  }
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
  connectedCallback() { if (!this.timer) this.timer = setInterval(() => this.render(), 1000); }
  disconnectedCallback() { clearInterval(this.timer); clearTimeout(this.saveTimer); this.timer = null; }
  getCardSize() { return 6; }
  get texts() {
    return (this._hass.locale?.language || this._hass.language || "en").startsWith("zh") ? {
      title: "电池强制充放电", charge: "充电", discharge: "放电", power: "功率", duration: "时长", minutes: "分钟",
      startCharge: "开始充电", startDischarge: "开始放电", update: "按新设置开始", stop: "停止", busy: "正在处理…",
      note: "设置后点击开始才生效。", runningNote: "修改数值不会影响当前任务，再次点击开始才更新。",
      idle: "无手动任务", charging: "充电任务运行中", discharging: "放电任务运行中", holding: "保持任务运行中",
      stopped: "手动任务已停止", completed: "手动任务已完成", unconfirmed: "执行结果未确认，请检查设备状态", unknown: "状态暂不可用",
      target: "本次设定", actual: "实际功率", until: "预计结束时间", noTask: "暂无任务", noEnd: "待确认", pending: "正在确认任务结束…",
      invalid: "请填写大于 0 的功率，以及 1～1440 的整数分钟。", powerLimit: "功率不能超过设备允许值",
      tou: "当前正在使用定时充放电，退出后才能手动操作。", leaveTou: "退出定时模式", unavailable: "设备或操作参数暂不可用，请检查连接。",
      access: "允许远程充放电", enable: "已关闭", disable: "已开启", offNote: "开启后恢复操作实体；点击开始才执行充放电。", onNote: "操作实体已启用。关闭时会先停止正在执行的任务。", syncing: "正在启用操作实体，请稍候。HA 完成加载后才能开始任务。",
      controlsOff: "尚未开启控制权限。请在集成配置 → 控制权限中开启“允许控制”；充放电权限仍需单独开启。",
    } : {
      title: "Forced battery charge / discharge", charge: "Charge", discharge: "Discharge", power: "Power", duration: "Duration", minutes: "min",
      startCharge: "Start charging", startDischarge: "Start discharging", update: "Start with new settings", stop: "Stop", busy: "Working…",
      note: "Settings take effect when you press Start.", runningNote: "Edits do not change this task. Press Start again to apply them.",
      idle: "No manual task", charging: "Charge task running", discharging: "Discharge task running", holding: "Hold task running",
      stopped: "Manual task stopped", completed: "Manual task completed", unconfirmed: "Execution unconfirmed; check the device", unknown: "Status unavailable",
      target: "This task", actual: "Actual power", until: "Estimated end time", noTask: "No active task", noEnd: "Unconfirmed", pending: "Waiting for exit confirmation…",
      invalid: "Enter positive power and a whole duration of 1–1440 minutes.", powerLimit: "Power exceeds the device limit",
      tou: "Time-of-use is active. Exit it before manual operation.", leaveTou: "Exit time-of-use mode", unavailable: "Device or operation settings unavailable. Check the connection.",
      access: "Allow remote charge / discharge", enable: "Off", disable: "On", offNote: "Enable to restore operation entities; press Start to run a task.", onNote: "Operation entities enabled. Disabling first stops any active task.", syncing: "Enabling operation entities. Wait for Home Assistant to load them before starting a task.",
      controlsOff: "Control access is off. Enable Allow control in the integration options; remote charge/discharge still requires its separate opt-in.",
    };
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
    if (this.busy || !this.accessEnabled || !this.config.work_mode_entity?.startsWith("select.")) return;
    this.busy = true; this.el("error").hidden = true; this.render();
    try {
      await this._hass.callService("select", "select_option", { entity_id: this.config.work_mode_entity, option: "self_consumption" });
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
    this.el("leave-tou").hidden = !tou || !this.config.work_mode_entity?.startsWith("select.");
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
