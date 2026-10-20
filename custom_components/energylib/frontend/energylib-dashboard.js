// EnergyLIB dashboard strategy: builds the EnergyLIB page from the user's own devices.
// Served by the integration at /energylib_static/; users add it from
// Settings > Dashboards > Add dashboard > EnergyLIB, or with
//   strategy:
//     type: custom:energylib
// Entities are found by device and translation key, never by entity ID, so
// renamed entities keep working. Controls appear only when they are enabled.

// Optional: the flow diagram uses power-flow-card-plus when the user installed it
// (HACS). It is not bundled: the upstream project publishes no license.
const FLOW_CARD = "power-flow-card-plus";

const TEXT = {
  en: {
    flow: "Live energy flow",
    flowTitle: "Power flow",
    flowMissing: "Some power values are unavailable; the flow appears again once the device reports.",
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
    controls: "Controls",
    remote: "Remote dispatch",
    remoteNote:
      "Remote power: **− = charge, + = discharge**. The setting runs for 300 s, then the inverter returns to self-consumption. Use the action `energylib.set_battery_power` for a custom duration.",
    controlsOff:
      "Controls are off. Enable **Allow control** in the integration options to change work mode, SOC limits and remote dispatch.",
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
    weekdays: { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" },
    charge: "Charge",
    discharge: "Discharge",
    noPeriods: "No periods.",
    activeDays: "Active",
  },
  zh: {
    flow: "实时能流",
    flowTitle: "实时功率流向",
    flowMissing: "部分功率数据暂不可用，恢复连接后自动显示流向。",
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
    controls: "运行控制",
    remote: "远程调度",
    remoteNote:
      "远程功率 **负为充电、正为放电**。设定后按该功率运行 **300 秒**，到期自动回到自发自用；自定义时长可用动作 `energylib.set_battery_power`。",
    controlsOff: "控制未开启。在集成选项中打开 **允许控制** 后，可以调整工作模式、SOC 和远程调度。",
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
    weekdays: { mon: "周一", tue: "周二", wed: "周三", thu: "周四", fri: "周五", sat: "周六", sun: "周日" },
    charge: "充电",
    discharge: "放电",
    noPeriods: "暂无时段。",
    activeDays: "生效",
  },
};

// Dashboard resources load in parallel with strategies; give them a moment.
async function hasFlowCard() {
  if (customElements.get(FLOW_CARD)) return true;
  const timeout = new Promise((resolve) => setTimeout(() => resolve(false), 2000));
  return Promise.race([customElements.whenDefined(FLOW_CARD).then(() => true), timeout]);
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

function buildView(hass, device, t, flowCard, multiple) {
  const ent = deviceEntities(hass, device.id);
  const s = (key) => ent[`sensor.${key}`];
  const name = (id) => hass.entities[id]?.name || undefined;
  const has = (...ids) => ids.every(Boolean);
  const heading = (text, icon) => ({ type: "heading", heading: text, icon, heading_style: "title" });
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

  // Live flow: the bundled power flow card when every power value exists.
  const flowCards = [heading(t.flow, "mdi:transit-connection-variant")];
  if (flowCard && has(...power)) {
    const [solar, load, battery, grid] = power;
    flowCards.push(
      {
        type: "conditional",
        conditions: power.map((entity) => ({ condition: "state", entity, state_not: ["unavailable", "unknown"] })),
        card: {
          type: "custom:power-flow-card-plus",
          title: t.flowTitle,
          entities: {
            solar: { entity: solar, name: t.solar, color: "#ffb300", color_icon: true, color_value: true },
            home: { entity: load, name: t.load, icon: "mdi:home-lightning-bolt", override_state: true, color_icon: true, color_value: true },
            battery: {
              entity: battery,
              name: t.batteryShort,
              state_of_charge: s("battery_total_soc"),
              display_state: "one_way",
              color_icon: "color_dynamically",
              color_circle: "color_dynamically",
            },
            grid: {
              entity: grid,
              name: t.grid,
              display_state: "one_way",
              color_icon: "color_dynamically",
              color_circle: "color_dynamically",
            },
          },
          clickable_entities: true,
          kilo_decimals: 2,
          base_decimals: 0,
          kilo_threshold: 0,
          use_new_flow_rate_model: true,
          display_zero_lines: { mode: "hide" },
        },
      },
      {
        type: "conditional",
        conditions: [
          {
            condition: "or",
            conditions: power.map((entity) => ({ condition: "state", entity, state: ["unavailable", "unknown"] })),
          },
        ],
        card: { type: "markdown", title: t.flowTitle, content: t.flowMissing },
      }
    );
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
    ["switch.grid_charge", "mdi:battery-charging-wireless", "purple", [{ type: "toggle" }]],
  ].map(([key, icon, color, features]) => tile(ent[key], icon, color, { features }));
  const remote = [
    tile(ent["number.remote_power"], "mdi:flash", "amber", { features: [{ type: "numeric-input", style: "slider" }] }),
    tile(ent["switch.remote_control"], "mdi:remote", "red", { features: [{ type: "toggle" }] }),
  ];
  const statusCards = compact([
    heading(t.status, "mdi:information-outline"),
    tile(s("running_state"), "mdi:power-plug", "green"),
    tile(s("active_alarms"), "mdi:alert-circle-outline", "red"),
    controls.some(Boolean) ? heading(t.controls, "mdi:tune") : tile(s("work_mode"), "mdi:cog-transfer", "indigo"),
    ...controls,
    remote.some(Boolean) && { type: "heading", heading: t.remote, icon: "mdi:remote", heading_style: "subtitle" },
    ...remote,
    remote.some(Boolean) && { type: "markdown", content: t.remoteNote },
    !controls.some(Boolean) && { type: "markdown", content: t.controlsOff },
  ]);

  const todayCards = compact([
    heading(t.today, "mdi:calendar-today"),
    stat(s("solar_energy"), t.todaySolar, "mdi:solar-power"),
    stat(s("load_energy"), t.todayLoad, "mdi:home-lightning-bolt"),
    stat(s("grid_import_energy"), t.todayImport, "mdi:transmission-tower-import"),
    stat(s("grid_export_energy"), t.todayExport, "mdi:transmission-tower-export"),
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
        `{% set s = states['${tou}'] %}{% set wd = ${wd} %}` +
        `{% if s and s.attributes.periods %}{% for p in s.attributes.periods %}- ` +
        `{{ '${t.charge}' if p.type == 'charge' else '${t.discharge}' }} {{ p.start }}–{{ p.end }}, {{ p.power }} kW\n` +
        `{% endfor %}\n${t.activeDays}: {% for d in s.attributes.weekdays %}{{ wd.get(d, d) }}{{ ', ' if not loop.last }}{% endfor %}` +
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

  const totalCards = compact([
    heading(t.totals, "mdi:chart-box"),
    tile(s("solar_energy"), "mdi:solar-power", "amber"),
    tile(s("load_energy"), "mdi:home-lightning-bolt", "blue"),
    tile(s("grid_import_energy"), "mdi:transmission-tower-import", "purple"),
    tile(s("grid_export_energy"), "mdi:transmission-tower-export", "cyan"),
    tile(s("battery_charge_energy"), "mdi:battery-arrow-up", "green"),
    tile(s("battery_discharge_energy"), "mdi:battery-arrow-down", "teal"),
    {
      type: "logbook",
      title: t.log,
      hours_to_show: 24,
      entities: compact([s("running_state"), s("active_alarms"), ent["select.work_mode"] || s("work_mode"), ent["switch.remote_control"], ent["switch.grid_charge"]]),
    },
  ]);

  const deviceName = device.name_by_user || device.name;
  return {
    title: multiple ? deviceName : "EnergyLIB",
    path: multiple ? device.id : "home",
    icon: "mdi:home-battery",
    type: "sections",
    max_columns: 3,
    sections: [
      { type: "grid", cards: flowCards },
      { type: "grid", cards: batteryCards },
      { type: "grid", cards: statusCards },
      { type: "grid", column_span: 2, cards: todayCards },
      { type: "grid", cards: touCards },
      { type: "grid", cards: totalCards },
    ],
  };
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
    const flowCard = await hasFlowCard();
    return {
      title: "EnergyLIB",
      views: devices.map((device) => buildView(hass, device, t, flowCard, devices.length > 1)),
    };
  }
}

if (!customElements.get("ll-strategy-dashboard-energylib")) {
  customElements.define("ll-strategy-dashboard-energylib", EnergyLibDashboardStrategy);
}
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
window.energyLibDashboard = { buildView, deviceEntities, energyLibDevices, TEXT };
