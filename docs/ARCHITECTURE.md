# 架构计划

> 实施状态：`energylib_core`、profile 接口、读取计划和各 HA 平台都已实现。目前只有一个盛弘 V001B031 profile，真机和模拟器共用；控制按协议寄存器实现、需用户手动开启，尚未经过实机验证。旧 protocol 导入作为兼容包装保留。

状态：单台逆变器、标准 Modbus TCP 的开发预览版，已在 HA 2026.8.2 上用软件模拟器完成发现、添加、读取和控制验证（见 [TESTING.md](TESTING.md)）；物理硬件验证待完成。

## 分工

1. **发现和配置**：mDNS 服务匹配、实际读取 SN、用户确认、稳定设备 ID、手动地址备用入口；初版无额外认证。
2. **通信层**：管理真实协议连接、请求超时、重连、并发限制和数据有效性；不依赖 HA 实体。
3. **型号适配层**：根据经验证的型号/协议版本解码字段并声明能力；不支持的型号明确拒绝。
4. **数据协调器**：统一刷新设备快照，避免各个实体重复请求。
5. **HA 实体**：根据能力创建传感器及可选的 select/number/switch/time 等原生实体。
6. **诊断与维护**：脱敏诊断、告警、版本/型号支持矩阵、升级与回滚文档。

## 运行代码

```text
custom_components/energylib/
├── manifest.json
├── __init__.py
├── config_flow.py
├── const.py
├── coordinator.py
├── sensor.py
├── diagnostics.py
├── strings.json
├── translations/
└── protocol/
```

通信层基于标准库 asyncio，只提供 FC03。单机基础点表和解码与 HA 分离；统一协调器负责读取，sensor 平台创建只读实体。控制平台文件在写接口完成验证后再加入。

## 多厂家通用设计

目标：以后接入其他品牌逆变器时，**只新增一个厂家适配文件（profile），不改 HA 实体层，也不改用户界面**。盛弘 V001B031 作为第一个 profile。

### 分层

```text
custom_components/energylib/
├── (HA 层) config_flow / options_flow / coordinator / sensor / select / number / switch / services
└── energylib_core/          # 不 import homeassistant，以后可以整体拆成 PyPI 库（进 HA Core 必须这样）
    ├── transport/           # 怎么收发：modbus_tcp（FC02/03/04/16）、以后加 rtu_over_tcp、http、mqtt
    ├── model/               # 统一模型：标准测点、能力、工作模式、TOU、告警
    ├── planner.py           # 把已启用的测点合并成读取块
    └── profiles/
        ├── base.py          # Profile 接口
        └── sinexcel_v2.py   # 盛弘 V001B031：点位映射 + 识别 + 控制实现
```

| 层 | 只负责 | 不允许 |
|---|---|---|
| transport | 字节收发、超时、重连、串行化 | 知道任何寄存器的含义 |
| profile | 地址 / 类型 / 缩放 / 符号 → 标准测点；识别型号；声明能力；把标准控制翻译成厂家写入 | 创建 HA 实体 |
| model | 标准测点表（key、单位、HA 元数据、默认是否启用）、标准枚举、标准 TOU 结构 | 出现任何厂家地址 |
| HA 层 | 按“标准测点 ∩ profile 能力”创建实体；配置流程；服务 | 出现厂家判断（`if vendor == ...`） |

### 标准测点（所有厂家共用）

[POINT_LIST.md](POINT_LIST.md) B、C 两节的 key（`solar_power`、`grid_import_energy`、`battery_soc` 等）就是**标准测点**，在 `model/` 中只定义一次：单位、device_class、state_class、翻译、默认是否启用都在这里定。各厂家 profile 只做映射，不能另起名字。

**标准单位和符号**（profile 负责换算到这套标准）：

| 量 | 单位 | 符号约定 |
|---|---|---|
| 功率 | kW | `grid_power` 正 = 买电，负 = 卖电；`battery_power` 正 = 放电，负 = 充电；`solar_power` ≥ 0 |
| 电量 | kWh | 累计量单调递增；设备清零时由协调器识别，不改写历史 |
| SOC / SOH | % | 0~100 |

> 符号约定与 HA 能源面板的功率传感器保持一致。盛弘的 0x1272/0x1276 方向待实机确认，profile 里用 `invert` 标出。

**厂家独有的数据**：先考虑能不能归到某个标准测点；确实不能的，profile 里登记为 `extra`，默认禁用，或只放进诊断信息下载。这样默认界面在不同品牌之间保持一致。

### 能力（Capability）

profile 在识别设备后返回一个能力集合，HA 层只看能力，不看品牌：

```text
读取类：BATTERY、BATTERY_2、THREE_PHASE、GENERATOR、AC_COUPLE、EXTERNAL_METER、ALARMS
控制类：CTRL_WORK_MODE、CTRL_BACKUP_SOC、CTRL_MIN_SOC、CTRL_GRID_CHARGE、CTRL_EXPORT_LIMIT、CTRL_TOU、CTRL_REMOTE_POWER
```

实体创建条件 = 标准测点所需的能力 ⊆ 设备能力，并且 profile 映射了这个测点。

### 标准控制模型

| 标准控制 | 统一的形式 | 厂家差异放在 profile 里 |
|---|---|---|
| 工作模式 | 标准枚举：`self_consumption` / `time_of_use` / `backup` / `feed_in_priority` / `pv_charge_priority` / `peak_shaving` / `remote` | profile 维护“厂家值 ↔ 标准值”对照表；没有对应标准值的厂家模式显示为 `other`，只读 |
| SOC 限制 | `backup_soc`、`min_soc`、`max_soc`（%） | 华为的“放电截止”、思格的 cut-off 等都映射到这里 |
| 出口限制 | `export_limit`（kW） | 盛弘是防逆流使能 + 功率；思格是 40038 |
| 远程充放电 | `set_battery_power(power_kw, duration)` | profile 实现进入 / 保活 / 退出：盛弘用心跳 + 0xC646/0xC648；思格用 40029/40031/40032 |
| TOU | 标准 `TouSchedule` + 每个 profile 声明的 `TouLimits`（见 [TOU_DESIGN.md](TOU_DESIGN.md) 第 11 节） | 编码和解码在 profile 里 |

### 识别与配置流程

1. 用户在配置流程中选择品牌，或由发现机制（zeroconf / DHCP）带入品牌。
2. 依次调用候选 profile 的 `identify()`，读出 SN、型号、协议版本；读不到就明确拒绝。
3. 配置条目里保存 `profile_id` 和 `profile_version`。profile 升级时通过配置条目迁移处理，实体身份不变。
4. 设备唯一 ID 改为 `{厂家}_{SN}`，避免不同品牌的 SN 撞号。现有开发预览版的配置条目需要一次迁移（目前没有正式用户，越早改越好）。

### 读取计划

`planner.py` 按 (功能码, 从机) 分组，把**已启用实体**用到的地址合并成块。块的最大长度、允许的空洞大小、读到空洞时的行为都由 profile 声明。例如盛弘的预留地址返回什么尚未确认，就先设 `allow_gaps=False`。轮询频率按测点类别分档（功率快、电量中、设置和版本慢）。

### 新增厂家的步骤

1. 新建 `profiles/<厂家>_<协议>.py`：识别、点位映射、能力、控制实现。
2. 提供该厂家的寄存器快照夹具，通过**统一的 profile 契约测试**：必需测点齐全、单位和符号符合标准、不支持的测点显示为不可用而不是 0、控制写入后有回读。
3. 在支持矩阵里登记已验证的型号和固件。
4. HA 层、翻译文件、界面都不需要改；只有新增标准测点时才改 `model/`。

### 参考的多品牌项目

- [wills106/homeassistant-solax-modbus](https://github.com/wills106/homeassistant-solax-modbus)：一个集成通过“每个品牌一个插件”支持 SolaX、Growatt、Sofar、Solis 等多个品牌，和本方案的 profile 思路相同。
- [davidrapan/ha-solarman](https://github.com/davidrapan/ha-solarman)：用 YAML 描述每种逆变器的点表。

本方案采用**点位声明式 + 行为写代码**：点位用 Python 数据结构声明，方便测试和类型检查；心跳、TOU 编码这类逻辑写成代码钩子。纯 YAML 表达不了这些逻辑。

> 进 HA Core 时要注意：Core 一般按品牌或协议来划分集成。多品牌的通用集成更适合走 HACS；到时可以把 `energylib_core` 发布成库，再给单个品牌提交 Core 集成。

## 设计边界

- mDNS 是发现入口，不承载完整遥测和控制，不替代认证。
- 不因在局域网发现服务就自动信任设备的身份或控制能力。
- 型号差异通过显式能力映射处理，保持用户已存在的设备/实体身份稳定。
- 读取错误与真实零值分开；不支持的测点不创建或明确保持不可用。
- 控制写入应与读取协调，完成回读才更新为成功状态。
- 调度、逆变器本机策略及其他控制源的优先级需明确，避免多方互相覆盖。
- HEMS 可以读取本集成的数据和调用已验证控制；集成本身不默认包含 EMHASS 等调度服务。
- HA 自动发现的标准流程由已安装的集成声明匹配规则；无需修改 HA 核心。

## 验证方向

发现/配置测试覆盖重复设备、地址变化、认证失败和不支持版本。协议测试覆盖缩放、符号、异常响应及计数器行为。实体测试覆盖设备分组、单位、统计语义和不可用状态。控制测试覆盖边界值、拒绝、回读不一致、断线和调度冲突。

已加入本机 TCP 测试、HA API 替身的配置流程契约测试及 Ruff 检查，命令见根目录 README 的“Development”一节。CI 不代表真实 HA 验证；目前声明的最低 HA 版本是已验证过的 2026.8.2（`hacs.json`）。
