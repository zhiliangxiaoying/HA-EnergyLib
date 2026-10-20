# 安装、能源配置、测试机与回滚

## 安装与升级

1. 备份旧组件及 HA 配置目录；升级前保存配置条目、设备/实体注册表和 Recorder 数据库的一致备份。
2. 将 `custom_components/energylib` 整个目录复制到 HA 配置目录的同名位置，重启 HA。
3. v1 配置会迁移为 v2：身份增加稳定命名空间，已有 entity_id 和设备 ID 保持不变。
4. 新安装手动输入地址、实际端口、设备地址，物理设备选择 EnergyLIB；发现仍复用 HA 的 Zeroconf。
5. 若迁移检测到配置/设备/实体冲突，会拒绝加载，不自动重建设备；使用备份排查，不能删实体掩盖问题。

手动添加的 Modbus TCP 端口默认填写 **502**，可按设备实际配置修改。
自动发现使用设备广播的端口；连接失败返回表单后保留用户填写的端口。现有设备配置不受默认值影响。
Modbus 设备地址使用数字输入框，默认 **1**，仅接受 1～255 的整数。

旧电池 1 SOC 不改含义；新版总 SOC 是单独实体。单电池且能力已证实时才允许回退。
未知测点和非法值显示不可用；连接故障不补 0、不回填历史。

## 能源面板

在“设置 → 仪表盘 → 能源”中选择有效的累计购电、馈电、光伏、充电和放电 kWh 传感器；
负载累计电量可用于独立展示，勿重复计入能源平衡。先与屏显/App 核对数值和计量方向。
累计电量使用 `total_increasing`；下降按设备实际复位处理，插件不加偏移或修改历史。
长期统计由 Recorder 生成，并非每次前端刷新实时计算；未知/不可用不能靠手改数值修复。

## 盛弘逆变器模拟器

`tools/sinexcel_simulator.py` 按盛弘 V001B031 协议模拟一台逆变器：寄存器地址、FC02/03/04/16、Unit 1/2、
额定功率 0x2008、心跳 0xC068~0xC06C、定时充放电 0xC014~0xC0D0 都和协议一致。HA 里**和真机一样**用 EnergyLIB 添加，
没有单独的“测试台”选项。协议没写清楚的地方，按 [FIRMWARE_QUESTIONS.md](FIRMWARE_QUESTIONS.md) 中的同一套假设实现。

```powershell
.\.venv\Scripts\python.exe -m tools.sinexcel_simulator --bind 192.0.2.10 --port 15021 --allow-peer 192.0.2.20 --enable-writes --mdns
```

- `--mdns`：广播 `_energylib._tcp.local.`（`--bind` 必须是具体的局域网地址），HA 的“已发现”里会出现，点“添加”即可。
- `--enable-writes`：允许写入，模拟器默认只读。HA 中还要打开“允许控制”，再逐个启用控制实体。
- `--rated-kw`：额定功率（默认 5，写在 0x2008），决定出口限制、远程功率和 TOU 功率的上限。
- 已经添加过的设备再次被发现时，会核对身份后更新地址，不会重复弹出提示。
- 每个实例使用独立的 SN、端口和 `--state` 文件。
- 旧的只读展示模拟器 `tools/inverter_simulator.py` 和发现测试工具 `tools/mdns_peer.py` 保留；它们没有控制寄存器，所以不会出现控制实体。

可选场景：`--machine-type 1` 单相、`--pv-count 4`、`--batteries 2`、`--no-bms`、
`--port-use 1`；故障注入 `--fault unit2_offline/reject_write/mismatch/disconnect/short_frame`，
或 `--fail-write-index N` 在第 N 次写入断线。星期/看门狗测试使用可注入时钟，不加快真实设备时钟。

## 控制与 TOU

控制按盛弘协议寄存器实现，**尚未经过实机验证**（诊断信息中 `controls_hw_verified=false`）。
设备能读到全部控制寄存器时才会出现控制实体；功率上限取自设备的额定功率 0x2008。
通过集成选项打开权限后，7 个控制实体默认禁用；关闭权限会卸载控制实体。
原工作模式 sensor 保留，另加 select，因此控制开启后的数量不沿用旧文档“替换 sensor”的计算方式。

`set_battery_power` 必须指定秒数 `duration`（1～86400），负功率充电、正功率放电。
直接打开远程控制开关或设置远程功率 number 会建立 300 秒有限租约；HA 重启不恢复租约。
远程控制期间每 10 秒向 0xC068 写 1.0 作为心跳，超时时间 60 秒，失联后设备按 0xC06C 回退到自发自用（心跳写法待厂家确认，见 Q3）。

TOU 在选项中编辑草稿、预览、保存；修改草稿不会写入。服务为 `set_tou_schedule` 和
`clear_tou_schedule`，均要求 `device_id`。不使用旧名 `set_schedule`。
整表多帧写入不是原子事务：先把工作模式切到自发自用，再逐块写入并回读，完全一致后才恢复原模式或激活 TOU；
失败显示不确定状态并读取实际参数，禁止自动恢复旧计划；失败的草稿会结束，重新进入才能读取设备实际计划。
双电池参数的 number 属性包含 battery_2_value 和 batteries_match，第一块成功、第二块失败时可见差异。
关闭失败后以设备看门狗为准。
`activate` 默认 false，省略时段外模式保持原值，清空不隐式改模式。暂不支持跨零点的时段，
拒绝 24:00，不转换为 23:59。保存和清空均保留设备已有的生效起止日期；日期不属于当前编辑接口，
计划传感器属性给出 `valid_from` / `valid_until`，状态也按该窗口判断。编码规则仍待厂家确认（Q5、Q10）。

重连后的控制会先重新识别能力，再在设备锁内校验最新功率上限。卸载后的运行对象拒绝后续读取和写入，
需要重新加载集成来创建新的运行对象；旧引用不能重新连接或启动远程租约。

## 检查与排障

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

真实 HA 检查：在 HA 镜像中从仓库根目录执行 `python -m tools.ha_runtime_check`。
它创建临时配置和回环测试机，不接触正在运行的配置、不启动 HTTP 或第二套 mDNS。
诊断下载只包含版本、能力、可用点名及命令状态，不导出 SN、地址、原始寄存器和用能读数。
当前告警的 `complete=false` 表示部分来源不可读，不能解释为“无告警”。

## 回滚

停止 HA 后恢复旧组件，以及同一备份时刻的配置条目和设备/实体注册表，再启动；不能只降级 Python 文件，
因为 v0.1 不认识 v2 身份。升级不会改 Recorder 历史；若恢复数据库，必须使用与备份一致的完整恢复流程。
不要为了回滚清零电量、修改统计或者删除原设备。详细日志与备份仅本地保存。

## 发布边界

代码以 Apache-2.0 开源（版权人 zhiliangxiaoying，见 `LICENSE` 与 `NOTICE`）；厂家协议原件不随代码分发。
`hacs.json`、hassfest/HA 版本矩阵和手动 HACS 校验是准备工作，不等于上架完成。
公开仓库、创建正式 Release、提交 HACS 默认商店由仓库所有者操作；发布说明标注“开发预览、控制未经实机验证”。
