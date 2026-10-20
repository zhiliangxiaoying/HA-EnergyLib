# EnergyLIB 局域网发现约定

状态：单机只读发现流程已实现，在真实 HA 2026.8.2 上通过独立电脑发布的 DNS-SD 服务完成发现与原生确认添加。物理设备固件仍待联调。用户已确认业务协议为标准 Modbus TCP。

## 服务记录

| 项目 | 约定/示例 |
| --- | --- |
| 服务类型 | `_energylib._tcp.local.` |
| 实例名 | `EnergyLIB-<device_id>` |
| 主机名 | `energylib-<device_id>.local.` |
| 地址 | 当前可连接的 IPv4/IPv6 地址，通过 A/AAAA 记录解析 |
| SRV 端口 | 实际 Modbus TCP 端口，不默认 502、80 或 443 |
| TXT `id` | 可选，当前须与业务接口读出的逆变器 SN 一致；不填通讯棒的不同 SN |
| TXT `model` | 型号标识，建议稳定的机器可读字符串 |
| TXT `api_ver` | 可选，当前发现合同版本为 `1`；不是 ARM 固件版本 |
| TXT `manufacturer` | 可选，`EnergyLIB` |
| TXT `fw` | 可选，固件版本 |
| TXT `transport` | 可选，当前仅支持 `modbus_tcp` |
| TXT `auth` | 可选，当前仅支持 `none`；不得携带任何秘密 |

这些 TXT 键是项目建议，不是 Home Assistant 或 mDNS 标准为逆变器定义的通用字段。mDNS 端口 UDP 5353 与 SRV 中的业务服务端口是两个概念。

## 身份与网络行为

- `id` 必须在重启、固件升级、DHCP 地址变化后保持稳定；设备信息接口应返回相同 ID。
- 同一台设备的 Wi-Fi 和以太网地址应归属于同一设备身份，避免产生重复设备。
- 多设备实例名和主机名应避免冲突；应按 mDNS/DNS-SD 标准处理冲突、缓存、服务更新和退出。
- 服务应在业务接口可用时发布；网络重新连接后恢复响应。不要只发一次公告而不响应后续查询。
- 初始部署假设 HA 与设备位于多播可达的局域网；跨 VLAN 需要网络层正确转发，插件不能绕过隔离。
- 仅实现 `.local` 名称解析、没有服务注册时，需要用户提供完整主机名/IP，或另行设计发现机制。

## HA 端实现

manifest.json 中已声明：

```json
"zeroconf": ["_energylib._tcp.local."]
```

以上为 manifest 中的发现片段，完整开发预览组件位于 `custom_components/energylib/`。

配置流程接收 ZeroconfServiceInfo，解析主机、端口和 TXT；实际读取逆变器 SN 并核对 TXT ID；根据 SN 去重，用户确认后创建配置。再次发现时经业务接口核对后更新连接地址，不改变设备和实体 ID。没有 TXT ID 时可读 SN 去重；若同时使用非默认 Modbus 地址，需要在确认页修改地址再读取。

当前仅支持无认证普通 Modbus TCP，不实现额外认证或 TLS，SN 核对不是加密认证。TXT 型号、固件只是提示，不能作为已验证的设备能力。

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
