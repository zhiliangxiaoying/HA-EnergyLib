# 本机逆变器开发服务

> 此脚本保持旧的只读合同（v0.1 的 11 个点）。带控制的盛弘协议模拟器是 `python -m tools.sinexcel_simulator`，默认拒绝写入；场景、限制和运行方式见 [OPERATIONS.md](OPERATIONS.md)。不要复用当前展示进程的状态文件。

`tools/inverter_simulator.py` 是独立于 HA 的单机开发设备：电脑发布 `_energylib._tcp.local.` DNS-SD 服务，HA 插件通过标准 Modbus TCP 读取。界面名称为 EnergyLIB，开发资料明确记录数据来源，不作为真实硬件验证证据。

## 运行

在项目虚拟环境中安装 `requirements-simulator.txt`，再启动脚本。下面使用文档示例地址，运行前替换为电脑和 HA 的实际局域网地址：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-simulator.txt
.\.venv\Scripts\python.exe tools/inverter_simulator.py --bind 192.0.2.10 --port 15020 --allow-peer 192.0.2.20
```

15020 是开发服务选择的端口，不是实际产品的默认端口。仅绑定指定网卡，仅接受允许的 HA 地址、本机地址和回环地址的 TCP 连接。设备地址为 1，服务 TXT ID 与开发设备 SN 一致。

独立电脑上的服务使用自己的 Zeroconf 实例发布；HA 集成仍完全复用 HA 的共享 mDNS 实现，不在 HA 里另开协议栈。

## 数据与生命周期

- 白天光伏随日照、日期和云量变化，家庭负载持续变化，储能按剩余功率和 SOC 充放电。
- 寄存器按盛弘约定输出：电池功率正数为充电，电网功率正数为馈电（卖电）。插件读取后转换为统一约定（电网正 = 买电，电池正 = 放电）。
- 瞬时满足光伏 + 电网 + 电池 = 负载，电量按实际运行时间积分，SOC 按容量和效率更新。
- 电量及 SOC 每 5 秒写入本地 `local_data/inverter-state.json`，重启后延续；停机期间不补写历史。
- 运行信息在 `local_data/inverter-status.json`。日志、状态、部署记录和凭据不进入 Git。
- 只实现读取，任何写功能码均返回异常，不改变数据模型。
- 电脑关机、休眠、进程退出或网络不通时 HA 会显示不可用。当前部署为后台进程，没有安装 Windows 系统服务或配置开机自启。
- 电脑网卡地址变化后需用新地址重启服务；稳定 SN 不变，HA 可在重新发现并核对身份后更新连接地址。

## 发现测试用模拟逆变器（tools/mdns_peer.py）

`tools/mdns_peer.py` 复用上面的数据模型和只读服务，原展示进程不受影响；不同的是 SN、端口和 TXT 字段都可以配置，用来测试 HA 的自动发现流程。
每个实例都是一台独立的设备，可以同时启动多台。

```powershell
# 正常设备（TXT id 与 SN 一致）
.\.venv\Scripts\python.exe -m tools.mdns_peer --serial ELIB260101 --bind 192.0.2.10 --port 15030 --allow-peer 192.0.2.20 --state local_data/peer-260101.json
# 地址变化：同一 SN 换一个端口重新启动，HA 应自动更新原配置
.\.venv\Scripts\python.exe -m tools.mdns_peer --serial ELIB260101 --bind 192.0.2.10 --port 15031 --allow-peer 192.0.2.20 --state local_data/peer-260101.json
# 负面测试：广播的 ID 与 SN 不一致 / 不带 ID / 声明不支持的认证或传输方式
.\.venv\Scripts\python.exe -m tools.mdns_peer --serial ELIB260103 --txt-id ELIB999999 --bind 192.0.2.10 --port 15033 --allow-peer 192.0.2.20
.\.venv\Scripts\python.exe -m tools.mdns_peer --serial ELIB260105 --no-txt-id --bind 192.0.2.10 --port 15035 --allow-peer 192.0.2.20
.\.venv\Scripts\python.exe -m tools.mdns_peer --serial ELIB260104 --auth token --bind 192.0.2.10 --port 15034 --allow-peer 192.0.2.20
```

- 不加 `--state` 时，每次启动累计电量都从初始值算起，HA 会把这当作一次电表清零；测试地址变化或重启时，建议加上 `--state`。
- 用 Ctrl+C 正常退出时，进程会撤销 mDNS 广播；被强制结束时，广播会一直保留到缓存过期。
- 测试结束后，记得在 HA 中删除测试时建立的配置。
- 测试范围和结果摘要见 [TESTING.md](TESTING.md)。

## 已验证范围

见 [TESTING.md](TESTING.md)。模拟器测试不能证明任何物理型号、固件、硬件控制或跨 VLAN 组网的兼容性。
