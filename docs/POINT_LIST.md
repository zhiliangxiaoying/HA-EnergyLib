# 开发点表（定稿 v1）

> 实施修订：新增 battery_total_soc，不重用旧 battery_soc 的电池 1 含义；原工作模式 sensor 保留，控制另加 select。运行状态按“0x1001 起 3 位、低位在前”的假设解码，完整告警词典尚待核对（见 [FIRMWARE_QUESTIONS.md](FIRMWARE_QUESTIONS.md)）。C 节展开为 19 项，具体设备按能力裁剪。下面的地址是协议映射，不代表全部已获实机验证；以代码为准。

> 0.8.16.dev1：盛弘 profile 的 FC02 请求起始地址向下对齐到偶数，返回时丢弃补读的前导位。运行状态仍对应 0x1001～0x1003，线上请求改为从 0x1000 读 4 位；不强制读取数量为偶数。已是偶数起点的告警块 0x1048/202 位和电网汇总 0x1014/1 位不变。这是特定设备适配，不是 Modbus 标准要求。

> 范围：V001B031 单机。原则：用户可见的参数能少尽量少（默认显示 14 个实体）。
> 除特别注明外，数据类型均为 float32（2 个寄存器，ABCD），从机地址为 1。
> “⚠”表示上线前须实机确认的事项。
>
> **多厂家说明**：B、C、E 节的 key、单位、HA 类型属于**标准测点 / 标准控制**，所有厂家共用；“地址 / FC / 从机”这几列是**盛弘 profile 的映射**。以后接入其他品牌时只补该品牌的映射，key 不变。功率符号按标准约定（电网正 = 买电，电池正 = 放电），见 [ARCHITECTURE.md](ARCHITECTURE.md)“多厂家通用设计”。

## A. 身份与能力读取（不建实体，只用于设备信息和裁剪实体）

| 地址 | FC | 类型 | 用途 |
|---|---|---|---|
| 0xC102~0xC10D | 03 | ASCII 24 字节（12 个寄存器） | 逆变器 SN；去除尾随 NUL/ASCII 空格填充，保持 SN 大小写；拒绝前导/内部空格和非法字符 |
| 0x2000、0x2001、0x2002、0x2003 | 03 | U16 版本 | 协议 / DCAC / DCDC / ARM 版本，写入设备信息 |
| 0x2006 | 03 | U16 | 机器类型：0 三相、1 单相、2 裂相、3 离网，用于裁剪 B/C 相实体 |
| 0xC0F6 | 03 | float 枚举 | 机型名称，写入设备信息 |
| 0xC05E | 03 | float | MPPT 路数，决定创建几路 PV 实体 |
| 0x3036 | 03 | float | 电池 2 启用，决定是否创建电池 2 实体 |
| 0xC264 | 03 | float | 发电机口用途（禁止 / 发电机 / 智能负载 / AC 耦合），决定是否创建对应功率实体 |

## B. 默认显示：14 个（v0.2；告警项修订于 v0.8.5）

| # | key | 中文名 | 地址 | FC / 从机 | 单位 | HA 类型 | device_class / state_class |
|---|---|---|---|---|---|---|---|
| 1 | solar_power | 光伏总功率 | 0x1270 | 03 / 1 | kW | sensor | power / measurement |
| 2 | battery_power | 电池功率 | 0x1272 | 03 / 1 | kW | sensor | power / measurement ⚠方向 |
| 3 | grid_power | 电网功率 | 0x1276 | 03 / 1 | kW | sensor | power / measurement ⚠方向 |
| 4 | load_power | 负载功率 | 0x1274 | 03 / 1 | kW | sensor | power / measurement |
| 5 | battery_soc | 电池 SOC | 0xC644（不支持时退回 0x1212） | 03 / 1 | % | sensor | battery / measurement |
| 6 | solar_energy | 光伏发电量 | 0x125C | 03 / 1 | kWh | sensor | energy / total_increasing |
| 7 | grid_import_energy | 购电量 | 0x1240 | 03 / 1 | kWh | sensor | energy / total_increasing |
| 8 | grid_export_energy | 馈电量 | 0x1242 | 03 / 1 | kWh | sensor | energy / total_increasing |
| 9 | battery_charge_energy | 电池充电量 | 0x125E | 03 / 1 | kWh | sensor | energy / total_increasing |
| 10 | battery_discharge_energy | 电池放电量 | 0x1260 | 03 / 1 | kWh | sensor | energy / total_increasing |
| 11 | load_energy | 负载用电量 | 0x1262 | 03 / 1 | kWh | sensor | energy / total_increasing |
| 12 | running_state | 运行状态 | 0x1001（3 位） | 02 / 1 | — | sensor | enum：待机 / 就绪 / 离网 / 并网 |
| 13 | alarm_status | 设备告警 | 0x1048~0x1111（202 位）+ 0x1014 电网异常位 | 02 / 1 和 2（0x1014 仅从机 1） | — | sensor | enum；属性包含 `alarms`、数量、完整性及历史 |
| 14 | work_mode | 工作模式 | 0x300C | 03 / 1 | — | sensor | enum：自发自用 / 定时充放电 / 分时电价 / 灾备 / 手动控制 / 光伏优先充电 / 卖电优先 / 调压 / 削峰填谷 |

第 1~11 项中已有 10 项实现（缺 0xC644）；第 12~14 项需新开发，其中第 12、13 项依赖 FC02。

## C. 默认禁用，用户可手动启用：20 个（v0.3）

带“条件”的实体只在满足条件时创建。

| key | 中文名 | 地址 | FC / 从机 | 单位 | 条件 |
|---|---|---|---|---|---|
| grid_voltage_a | 电网 A 相电压 | 0x1000 | 03 / 1 | V | — |
| grid_voltage_b | 电网 B 相电压 | 0x1002 | 03 / 1 | V | 三相 |
| grid_voltage_c | 电网 C 相电压 | 0x1004 | 03 / 1 | V | 三相 |
| grid_frequency | 电网频率 | 0x1012 | 03 / 1 | Hz | — |
| battery_voltage | 电池电压 | 0x120C | 03 / 1 | V | — |
| battery_current | 电池电流 | 0x120E | 03 / 1 | A | ⚠方向 |
| battery_soh | 电池 SOH | 0xB108 | 03 / 1 | % | BMS 在线（0xB000=1） |
| battery_2_soc | 电池 2 SOC | 0x121C | 03 / 1 | % | 电池 2 启用 |
| battery_2_power | 电池 2 功率 | 0x121A | 03 / 1 | kW | 电池 2 启用 |
| battery_2_voltage | 电池 2 电压 | 0x1216 | 03 / 1 | V | 电池 2 启用 |
| battery_2_current | 电池 2 电流 | 0x1218 | 03 / 1 | A | 电池 2 启用；⚠方向同电池电流 |
| pv1_power ~ pv4_power | PV 1~4 功率 | 0x1204、0x120A、0x1280、0x1286 | 03 / 1 | kW | 按 MPPT 路数（PV5~PV10 见 D 节） |
| backup_load_power | 重要负载功率 | 0x1278 | 03 / 1 | kW | — |
| generator_power | 发电机功率 | 0x128C | 03 / 1 | kW | 0xC264 = 1 或 2 |
| ac_couple_power | AC 耦合功率 | 0x12A4 | 03 / 1 | kW | 0xC264 = 4 |
| inverter_temperature | 逆变器温度 | max(0x10DE~0x10E6) | 03 / 1 | ℃ | 5 路取最大值，合成一个实体 |

注：PV1~PV4 只在 MPPT 路数足够时创建，所以实体数不固定，最多 20 个（含电池 2 电压/电流）。原“电网异常”（0x1014）和“BMS 通讯异常”（0x1110/0x1111）两个诊断实体已并入设备告警（见 ALARMS.md）。

## D. 只进诊断信息下载（不建实体）

| 内容 | 地址 |
|---|---|
| 当日 / 当月电量 | 0x1264~0x126E、0x12E4、0x12E6 |
| PV5~PV10、各路电压 / 电流 / 负载率 | 0x1200~0x120A、0x127C~0x128A、0x12B0~0x12DE |
| 电网 / 负载 / 逆变三相全量 | 0x1006~0x10DC |
| DCDC 温度、母线、绝缘、漏电流 | 0x1220~0x1228、0x10EE、0x10F0、0x1230、0x1232、0x12E8、0x12EA、0x1290、0x1292、0x1156、0x128E |
| 外部电表 CH1 / CH2 | 0xA000、0xA100~0xA12C、0xA142~0xA16A、0xA300 |
| BMS 柜详情 | 0xB000~0xB012、0xB100~0xB168、0xB300~0xB356 |
| 光伏去向电量 | 0xC304~0xC30E |
| 监控告警字、通讯棒状态 | 0xC052、0xD000~0xD006 |
| 其余版本号 | 0x2004~0x2019 |

## E. 控制：opt-in，集成选项中打开“允许控制”后才创建

### v0.4：5 个

| key | 中文名 | 地址 | HA 类型 | 范围 / 选项 | 备注 |
|---|---|---|---|---|---|
| work_mode | 工作模式 | 0x300C | select（替换 B 节第 14 项的只读 sensor） | 0 自发自用、1 定时充放电、3 灾备、5 光伏优先充电、6 卖电优先、8 削峰填谷 | 2、4、7 只显示，不可选 |
| backup_soc | 备电 SOC | 0x314A（电池 2 同步写 0x314C） | number | 0~100 % | — |
| min_soc | 放电截止 SOC | 0x3046（电池 2 同步写 0x30B8） | number | 0~100 % | 集成按缓存值拒绝“最低 > 备电”的写入（电池 2 分别比较）；设备自身是否约束待确认 ⚠ |
| grid_charge | 允许电网充电 | 0x322A | switch | 0 / 1 | — |
| export_limit | 出口功率限制 | 0x3004 | number | 0~机型额定功率 kW | 只写限值，不自动打开限制开关（0.8.33 起与 0x3002 分开）。限制开启时 0 kW 表示禁止馈网，不是“不限制” |
| export_limit_enable | 启用出口限制 | 0x3002 | switch | 0 / 1 | 关闭 = 不限制馈网，保留 0x3004 的限值；设备上的其他非 0 模式显示为开启（0.8.23） |

### v0.5：2 个实体 + 2 个服务

| key | 中文名 | 地址 | HA 类型 | 说明 |
|---|---|---|---|---|
| remote_control | 远程充放电控制 | 0xC646 | switch | 打开前先写 0xC06A 超时、0xC06C 逃生模式；运行期间维持心跳 ⚠保活方式 |
| remote_power | 远程充放电功率 | 0xC648 | number | -50~50 kW（负数充电，正数放电） |
| — | 服务 `set_battery_power` | 0xC646 + 0xC648 | service | 参数：功率、时长；时长到期自动退出 |
| tou_schedule | 定时充放电计划 | 0xC014~0xC042、0xC0B4~0xC0D0、0x314E | sensor | 状态是当前生效的时段（有效期已过显示“有效期已过”），属性是完整时段表；保存时若设备有效期已过，改为从当天起长期有效 |
| — | 服务 `set_tou_schedule`、`clear_tou_schedule` + 选项页面“定时充放电” | 同上 | service / options | 整表替换；详见 [TOU_DESIGN.md](TOU_DESIGN.md)；时段相关的 time / number 实体为 0 |

### 选项页参数（不建实体）

| 参数 | 地址 | 默认值 |
|---|---|---|
| 心跳超时 | 0xC06A | 60 s |
| 逃生模式 | 0xC06C | 1 自发自用 |
| 最大 SOC | 0x31B6 / 0x31B8 | 不改设备值 |
| 最大充 / 放电电流 | 0x3040 / 0x3042 | 不改设备值 |

## F. 汇总

| | 数量 |
|---|---|
| 默认可见实体 | 14 |
| 默认禁用实体 | 最多 20 |
| 控制实体（opt-in） | 7（work_mode 由只读换成可写，不额外计数；含 tou_schedule） |
| 服务 | 3 |
| 需新开发的读取能力 | FC02（从机 1、2）、按地址块读取、U16 / 枚举解码 |
| 需新开发的写入能力 | FC16 float 写入 + 回读（v0.4 起） |

## G. 读取分块（每轮刷新）

| 块 | FC / 从机 | 范围 | 说明 |
|---|---|---|---|
| 1 | 03 / 1 | 0x1240~0x1279 | 电量 + 总功率 + 重要负载 |
| 2 | 03 / 1 | 0xC644~0xC645 | 总 SOC |
| 3 | 03 / 1 | 0x300C~0x300D | 工作模式（60 s 刷新一次） |
| 4 | 02 / 1 | 0x1001~0x1003 | 运行状态 |
| 5 | 02 / 1、2 | 0x1048~0x1111 | 告警位 |
| 6 | 02 / 1 | 0x1014 | 电网异常位（设备支持时才读） |
| 其余 | 03 / 1 | 按已启用的实体计算 | 禁用的实体不读 |

⚠ 协议示例写的是“读 0x1000~0x1062 的 50 个寄存器，返回 200 字节”，数量字段到底按 16 位寄存器计还是按 float 点计，需要实机确认。
