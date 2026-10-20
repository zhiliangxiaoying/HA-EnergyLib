# EnergyLIB Home Assistant 集成

面向 **EnergyLIB 混合逆变器**的 Home Assistant 本地集成。逆变器使用盛弘 **V001B031** Modbus 协议，
通过通讯棒提供 **Modbus TCP**。集成读取功率、电量和电池数据，供能源面板使用，
并提供可选的控制功能（工作模式、SOC 限制、出口限制、远程充放电、定时充放电）。

[English](README.md)

> **开发预览版。** 目前只用软件模拟器测试过，尚未在物理逆变器上验证。控制功能按协议实现，
> **未经实机验证**，默认关闭，需要手动开启。详见 [测试说明](docs/TESTING.md) 和
> [待确认的协议问题](docs/FIRMWARE_QUESTIONS.md)。

## 功能

- 通过 mDNS（`_energylib._tcp.local.`）自动发现，也可以手动添加
- 读取逆变器 SN 核对身份；设备换地址后不会重复添加
- 默认显示 14 个实体：光伏、电池、负载、电网功率，6 项累计电量，电池总 SOC，运行状态，当前告警，工作模式
- 其他诊断实体默认禁用：电网电压与频率、电池电压/电流/SOH、各路光伏、温度、故障
- 可选控制（需手动开启）：工作模式、备电 SOC、放电截止 SOC、允许电网充电、出口功率限制、有时限的远程充放电
- 定时充放电编辑（充电、放电各最多 6 段），以及供自动化使用的服务
- 读不到的数据显示“不可用”，不会显示成 0；诊断信息已脱敏

只支持单台逆变器，不支持并机。

## 运行要求

- Home Assistant **2026.8.2** 或更新版本
- 通讯棒能在局域网访问，并提供 Modbus TCP（端口和设备地址以设备设置为准）
- 自动发现需要 HA 和设备之间能传递多播（mDNS）；不行的话可以手动添加

## 安装

**HACS（自定义仓库）**，在进入 HACS 默认商店之前：

1. HACS → 右上角 ⋮ → **自定义存储库** → 填入本仓库地址，类别选 **Integration**。
2. 搜索 **EnergyLIB**，下载后重启 Home Assistant。

**手动安装**：把 `custom_components/energylib` 复制到 HA 配置目录的 `custom_components/` 下，重启 HA。

## 配置

- **自动发现**：在“设置 → 设备与服务”的“已发现”里会出现 **EnergyLIB**，点“添加”，核对地址后提交。
- **手动添加**：“添加集成 → EnergyLIB”，填写 IP、Modbus TCP 端口和设备地址（默认 1）。

### 能源面板

使用这几个累计电量：购电量、馈电量、光伏发电量、电池充电量、电池放电量。使用前请先和逆变器屏幕上的数值核对。

### 功率正负

| 实体 | 正数 | 负数 |
|---|---|---|
| 电网功率 | 买电 | 卖电 |
| 电池功率 | 放电 | 充电 |

逆变器上报的方向正好相反（电池正数为充电，电网正数为馈电），集成已做转换。

## 控制（需手动开启）

1. “设置 → 设备与服务 → EnergyLIB → 配置 → 控制权限”，打开**允许控制**。
2. 只有逆变器能读到全部控制寄存器时才会出现控制实体。这些实体**默认禁用**，需要在设备页里逐个启用。

| 实体 | 作用 |
|---|---|
| 工作模式（选择） | 自发自用、定时充放电、备电、光伏优先充电、卖电优先、削峰填谷 |
| 备电 SOC / 放电截止 SOC（数值） | 电池保留电量和放电下限（截止 SOC 不能高于备电 SOC） |
| 允许电网充电（开关） | 是否允许用电网给电池充电 |
| 出口功率限制（数值） | 最大馈网功率，上限为逆变器额定功率 |
| 远程充放电控制 + 远程充放电功率（开关 / 数值） | 按设定功率充电（负）或放电（正）300 秒，到期回到自发自用 |

远程控制期间会持续发送心跳；HA 停止后，逆变器会在超时后自动回到自发自用。

### 定时充放电

“配置 → 定时充放电”：添加 / 编辑 / 删除时段（类型、开始、结束、功率），设置“星期与时段外模式”，
最后点“预览并保存”（可选择同时切换为定时充放电模式）。限制：充电、放电各最多 6 段；整张表共用一套星期；
时段不能重叠；不支持跨零点；不能写 `24:00`。

### 服务

```yaml
action: energylib.set_tou_schedule
data:
  device_id: <EnergyLIB 设备>
  periods:
    - {type: charge, start: "01:00", end: "06:00", power: 3}
    - {type: discharge, start: "18:00", end: "22:00", power: 2.5}
  weekdays: [mon, tue, wed, thu, fri]
  outside_mode: self_consumption
  activate: true
```

- `energylib.set_battery_power`：`power_kw`（负数为充电）和 `duration`（秒）
- `energylib.clear_tou_schedule`：清空所有时段

每次调用 `set_tou_schedule` 都会替换整张时段表。

## 排障

- “设置 → 设备与服务 → EnergyLIB → ⋮ → 下载诊断信息”：包含探测到的能力和最近一次命令状态，不含 SN 和地址。
- “当前告警”列出告警来源和寄存器代码；`complete: false` 表示有部分告警来源没读到，不代表“没有告警”。
- 已经不存在的设备仍出现在“已发现”里时，等 mDNS 缓存过期后会自动消失，期间可以点“忽略”。

## 开发

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check . && python -m ruff format --check .
python -m unittest discover -v
```

本地测试用的协议模拟器：`python -m tools.sinexcel_simulator --help`
（见 [docs/OPERATIONS.md](docs/OPERATIONS.md)、[docs/SIMULATOR.md](docs/SIMULATOR.md)）。
架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 免责声明

使用风险自负。修改逆变器参数会影响电池、馈网和备用供电，请遵守当地法规和厂家指导。
“盛弘 / Sinexcel”是其所有者的商标，此处仅用于说明协议兼容性；品牌图片归各自所有者所有。

## 许可证

[Apache License 2.0](LICENSE)，版权所有 © 2026 zhiliangxiaoying，另见 [NOTICE](NOTICE)。
厂家协议原件不包含在本项目中；许可证不涵盖品牌图片和商标。
