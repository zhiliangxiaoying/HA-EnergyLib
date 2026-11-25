"""V001B031 FC02 table, visually checked against local PDF pp. 46, 48–61.

The table marks machine types 1/2/3 DEFAULT; p46 identifies Unit 1 DCAC and
Unit 2 DCDC. Extended Unit coverage still needs firmware/hardware confirmation.
Page classification '提示' is informational, never promoted to fault/warning.
No classification is inferred from the wording (e.g. 10A2 is a warning).
This mapping does not assert physical model/firmware support.
"""

# Rows 1048–10DA, in address order. English is a UI translation, not vendor text.
# f=fault, w=warning, i=information. All rows apply to table machine types 1/2/3.
_ROWS = """
w|电网电压异常|Grid voltage abnormal
w|电网频率异常|Grid frequency abnormal
w|电网电压反序|Grid phase sequence reversed
w|电网电压缺相|Grid phase missing
f|输出电压异常|Output voltage abnormal
w|输出频率异常|Output frequency abnormal
f|零线异常|Neutral abnormal
w|环境温度过高|Ambient temperature high
f|散热器温度过高|Heatsink temperature high
f|绝缘故障|Insulation fault
f|漏电保护故障|Leakage protection fault
f|辅助电源故障|Auxiliary supply fault
f|风扇故障|Fan fault
f|机型容量故障|Model capacity fault
f|防雷器异常|Surge protector abnormal
w|孤岛保护|Islanding protection
i|电池1未接|Battery 1 disconnected
w|电池1过压|Battery 1 overvoltage
i|电池1欠压|Battery 1 undervoltage
w|电池1放电终止|Battery 1 discharge terminated
f|电池1反接|Battery 1 polarity reversed
i|电池2未接|Battery 2 disconnected
w|电池2过压|Battery 2 overvoltage
i|电池2欠压|Battery 2 undervoltage
w|电池2放电终止|Battery 2 discharge terminated
f|电池2反接|Battery 2 polarity reversed
i|光伏1未接入|PV 1 disconnected
f|光伏1过压|PV 1 overvoltage
i|光伏1均流异常|PV 1 current sharing abnormal
i|光伏2未接入|PV 2 disconnected
f|光伏2过压|PV 2 overvoltage
i|光伏2均流异常|PV 2 current sharing abnormal
f|直流母线过压|DC bus overvoltage
f|直流母线欠压|DC bus undervoltage
f|直流母线电压不平衡|DC bus voltage imbalance
f|光伏1功率管故障|PV 1 power transistor fault
f|光伏2功率管故障|PV 2 power transistor fault
f|电池1功率管故障|Battery 1 power transistor fault
f|电池2功率管故障|Battery 2 power transistor fault
f|逆变器功率管故障|Inverter power transistor fault
w|系统输出过载|System output overload
f|逆变器过载|Inverter overload
f|逆变器过载超时|Inverter overload timeout
w|电池1过载超时|Battery 1 overload timeout
w|电池2过载超时|Battery 2 overload timeout
f|逆变器软启动失败|Inverter soft start failed
w|电池1软启动失败|Battery 1 soft start failed
w|电池2软启动失败|Battery 2 soft start failed
f|DSP1参数设置故障|DSP 1 parameter fault
f|DSP2参数设置故障|DSP 2 parameter fault
f|DSP版本兼容故障|DSP version incompatibility
f|CPLD版本兼容故障|CPLD version incompatibility
f|CPLD通讯故障|CPLD communication fault
f|DSP通讯故障|DSP communication fault
f|输出电压直流量超限|Output DC voltage component excessive
f|输出电流直流量超限|Output DC current component excessive
f|继电器自检不通过|Relay self test failed
f|逆变器异常|Inverter abnormal
f|接地不良|Grounding fault
w|光伏1软启动失败|PV 1 soft start failed
w|光伏2软启动失败|PV 2 soft start failed
w|平衡电路过载超时|Balancing circuit overload timeout
w|光伏1过载超时|PV 1 overload timeout
w|光伏2过载超时|PV 2 overload timeout
w|PCB过温|PCB temperature high
w|直流变换器过温|DC converter temperature high
f|母线慢过压|Bus slow overvoltage
f|离网输出电压异常|Off grid output voltage abnormal
f|硬件母线过压|Hardware bus overvoltage
f|硬件过流|Hardware overcurrent
f|直流变换器过压|DC converter overvoltage
f|直流变换器硬件过压|DC converter hardware overvoltage
f|直流变换器过流|DC converter overcurrent
f|直流变换器硬件过流|DC converter hardware overcurrent
f|直流变换器谐振腔过流|DC converter resonant circuit overcurrent
f|光伏1反接|PV 1 polarity reversed
f|光伏2反接|PV 2 polarity reversed
i|电池1功率不足|Battery 1 insufficient power
i|电池2功率不足|Battery 2 insufficient power
w|电池1禁止充电|Battery 1 charging prohibited
w|电池1禁止放电|Battery 1 discharging prohibited
w|电池2禁止充电|Battery 2 charging prohibited
w|电池2禁止放电|Battery 2 discharging prohibited
i|电池1充满|Battery 1 full
w|电池1放电终止|Battery 1 discharge terminated
i|电池2充满|Battery 2 full
w|电池2放电终止|Battery 2 discharge terminated
w|负载功率过载|Load power overload
f|漏电自检异常|Leakage self test abnormal
w|逆变过温告警|Inverter temperature warning
w|逆变器过温|Inverter temperature high
w|直流变换器过温告警|DC converter temperature warning
w|并机通信告警|Parallel communication warning
w|系统降额运行|System derating
f|逆变继电器开路|Inverter relay open circuit
f|逆变继电器短路|Inverter relay short circuit
w|光伏接入方式错误告警|PV connection mode incorrect
f|并机模块缺失|Parallel module missing
w|并机模块机号重复|Parallel module number duplicated
w|并机模块参数冲突|Parallel module parameter conflict
w|光伏直流电弧故障|PV DC arc fault
w|电表接入异常|Meter connection abnormal
w|逆变器封脉冲|Inverter pulses blocked
i|光伏3未接入|PV 3 disconnected
f|光伏3过压|PV 3 overvoltage
i|光伏3均流异常|PV 3 current sharing abnormal
i|光伏4未接入|PV 4 disconnected
f|光伏4过压|PV 4 overvoltage
i|光伏4均流异常|PV 4 current sharing abnormal
f|光伏3功率管故障|PV 3 power transistor fault
f|光伏4功率管故障|PV 4 power transistor fault
w|光伏3软启动失败|PV 3 soft start failed
w|光伏4软启动失败|PV 4 soft start failed
w|光伏3过载超时|PV 3 overload timeout
w|光伏4过载超时|PV 4 overload timeout
f|光伏3反接|PV 3 polarity reversed
f|光伏4反接|PV 4 polarity reversed
w|发电机电压异常|Generator voltage abnormal
w|发电机频率异常|Generator frequency abnormal
w|发电机电压反序|Generator phase sequence reversed
w|发电机电压缺相|Generator phase missing
w|铅蓄电池温度异常|Lead acid battery temperature abnormal
w|电池接入方式错误|Battery connection mode incorrect
w|智能负载过载|Smart load overload
i|电池1达到备电SOC|Battery 1 at reserve SOC
i|电池2达到备电SOC|Battery 2 at reserve SOC
w|电网过载|Grid overload
w|发电机异常|Generator abnormal
f|母线软起失败|Bus soft start failed
f|电网继电器开路|Grid relay open circuit
f|电网继电器短路|Grid relay short circuit
f|发电机继电器开路|Generator relay open circuit
f|发电机继电器短路|Generator relay short circuit
w|光伏功率不足|PV insufficient power
w|光伏1过流|PV 1 overcurrent
w|光伏2过流|PV 2 overcurrent
w|光伏3过流|PV 3 overcurrent
w|光伏4过流|PV 4 overcurrent
w|电池1过流|Battery 1 overcurrent
w|电池2过流|Battery 2 overcurrent
w|电池1充电封波|Battery 1 charging pulses blocked
w|电池2充电封波|Battery 2 charging pulses blocked
f|负载继电器开路|Load relay open circuit
f|负载继电器短路|Load relay short circuit
f|EPO急停|EPO emergency stop
f|零火线反接|Line and neutral reversed
f|内部风扇故障|Internal fan fault
"""

_data = [line.split("|") for line in _ROWS.strip().splitlines()]
assert len(_data) == 0x10DB - 0x1048
for _pv in range(5, 11):
    _data.extend(
        [
            ["i", f"光伏{_pv}未接入", f"PV {_pv} disconnected"],
            ["f", f"光伏{_pv}过压", f"PV {_pv} overvoltage"],
            ["i", f"光伏{_pv}均流异常", f"PV {_pv} current sharing abnormal"],
            ["f", f"光伏{_pv}功率管故障", f"PV {_pv} power transistor fault"],
            ["w", f"光伏{_pv}软启动失败", f"PV {_pv} soft start failed"],
            ["w", f"光伏{_pv}过载超时", f"PV {_pv} overload timeout"],
            ["f", f"光伏{_pv}反接", f"PV {_pv} polarity reversed"],
            ["w", f"光伏{_pv}过流", f"PV {_pv} overcurrent"],
        ]
    )
_data.extend(
    [
        ["w", "逆变器N线过载超时", "Inverter neutral overload timeout"],
        ["w", "电网未接", "Grid disconnected"],
        ["w", "发电机未接", "Generator disconnected"],
        ["w", "并机通讯异常", "Parallel communication abnormal"],
        ["w", "电表通讯异常", "Meter communication abnormal"],
        ["w", "BMS1通讯异常", "BMS 1 communication abnormal"],
        ["w", "BMS2通讯异常", "BMS 2 communication abnormal"],
    ]
)
assert len(_data) == 202

# FC02 Unit 1 grid summary bit, outside the 1048–1111 table (formerly the
# "grid_fault" diagnostic entity). Named after the point list; the trigger
# conditions and the PDF page are not yet confirmed, so no page is cited and the
# severity follows the table's grid rows (warning).
GRID_ALARM_BIT = 0x1014


def describe_alarm(profile, function, unit, address):
    result = {
        "alarm_id": f"{profile}:fc{function:02}:u{unit}:{address:04X}",
        "source": unit,
        "function": function,
        "code": f"{address:04X}",
        "name": "未识别异常",
        "name_en": "Unrecognized alarm",
        "severity": "unknown",
        "evidence_page": None,
        "applicable_types": [],
    }
    if (
        profile == "sinexcel_v2"
        and function == 2
        and unit in (1, 2)
        and 0x1048 <= address <= 0x1111
    ):
        severity, zh, en = _data[address - 0x1048]
        page = 48 if address == 0x1048 else 49 + (address - 0x1049) // 16
        result.update(
            name=zh,
            name_en=en,
            severity={"f": "fault", "w": "warning", "i": "info"}[severity],
            evidence_page=page,
            applicable_types=[1, 2, 3],
        )
    elif profile == "sinexcel_v2" and function == 2 and unit == 1 and address == GRID_ALARM_BIT:
        result.update(name="电网异常", name_en="Grid abnormal", severity="warning")
    return result
