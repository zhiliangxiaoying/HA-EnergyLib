# EnergyLIB 集成代码位置

此目录预留给未来的 Home Assistant 集成实现，集成域计划为 `energylib`。

当前没有 manifest.json、配置流程或设备通信代码，不能作为 HA 集成安装。请先阅读根目录 README 和 docs/ 下的发现约定、架构计划及协议资料清单。

自动发现服务类型已约定为 `_energylib._tcp.local.`。收到固件实际发布记录和本地读写协议后，再建立适配代码及测试，不预设 Modbus 端口、寄存器或其他设备专有参数。
