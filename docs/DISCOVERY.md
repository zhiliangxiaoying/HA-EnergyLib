# EnergyLIB 局域网发现约定

状态：初始约定，待固件与 HA 插件联调。服务类型已经选定，其余自定义字段仍可在首次实现前共同确认。

## 服务记录

| 项目 | 约定/示例 |
| --- | --- |
| 服务类型 | `_energylib._tcp.local.` |
| 实例名 | `EnergyLIB-<device_id>` |
| 主机名 | `energylib-<device_id>.local.` |
| 地址 | 当前可连接的 IPv4/IPv6 地址，通过 A/AAAA 记录解析 |
| SRV 端口 | 实际业务接口的 TCP 端口；待确认，不默认 502、80 或 443 |
| TXT `id` | 固定的设备唯一 ID；建议序列号或出厂 UUID |
| TXT `model` | 型号标识，建议稳定的机器可读字符串 |
| TXT `api_ver` | 本地业务协议版本，例如 `1` |
| TXT `manufacturer` | 可选，`EnergyLIB` |
| TXT `fw` | 可选，固件版本 |
| TXT `transport` | 可选，但建议提供；业务协议确定后定义允许值 |
| TXT `path` | 可选；仅在所选业务协议有接口路径概念时使用 |
| TXT `auth` | 可选，认证方式名称；不得携带任何秘密 |

这些 TXT 键是项目建议，不是 Home Assistant 或 mDNS 标准为逆变器定义的通用字段。mDNS 端口 UDP 5353 与 SRV 中的业务服务端口是两个概念。

## 身份与网络行为

- `id` 必须在重启、固件升级、DHCP 地址变化后保持稳定；设备信息接口应返回相同 ID。
- 同一台设备的 Wi-Fi 和以太网地址应归属于同一设备身份，避免产生重复设备。
- 多设备实例名和主机名应避免冲突；应按 mDNS/DNS-SD 标准处理冲突、缓存、服务更新和退出。
- 服务应在业务接口可用时发布；网络重新连接后恢复响应。不要只发一次公告而不响应后续查询。
- 初始部署假设 HA 与设备位于多播可达的局域网；跨 VLAN 需要网络层正确转发，插件不能绕过隔离。
- 仅实现 `.local` 名称解析、没有服务注册时，需要用户提供完整主机名/IP，或另行设计发现机制。

## HA 端计划

未来 manifest.json 中声明：

```json
"zeroconf": ["_energylib._tcp.local."]
```

这只是 manifest 片段，不是完整、可安装的集成。

配置流程接收 ZeroconfServiceInfo，解析主机、端口和 TXT；验证支持的协议版本与真实设备身份；根据稳定 ID 去重；用户确认并完成认证后创建配置。再次发现时更新连接地址，不改变设备和实体 ID。

需要主动监听时，应通过 `homeassistant.components.zeroconf` 获取 HA 共享实例，不在插件中运行第二套 mDNS 服务。

## 联调验收

1. 设备先启动、HA 后启动，可以发现。
2. HA 先启动、设备后上线，可以发现。
3. 两台设备同时上线，独立显示且不重复。
4. 地址变化后更新原配置，历史实体保持不变。
5. 设备断电/网络中断后显示正确状态，恢复后可重新连接。
6. 缺失 ID、不支持的版本、伪造元数据、认证失败均明确处理。
7. 服务可以发现但业务端口不可达时，不生成虚假的在线读数。

## 标准与开发参考

- https://www.rfc-editor.org/rfc/rfc6762
- https://www.rfc-editor.org/rfc/rfc6763
- https://contributors.home-assistant.io/core/integrations/manifest/#zeroconf
- https://contributors.home-assistant.io/core/integrations/network-discovery/#zeroconf
- https://contributors.home-assistant.io/core/integrations/config-flow/
