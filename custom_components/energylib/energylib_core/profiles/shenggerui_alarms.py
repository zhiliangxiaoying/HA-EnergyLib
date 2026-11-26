"""Shenggerui inverter alarm bits 0x75A5-0x75AC (128 bits, bit n = register 0x75A5 + n // 16).

Names and levels from the manufacturer's alarm workbook (80 conditions, F01-F80
= bits 0-79), updated to table 1 of protocol V2.7.1.1 (2026-11-20): bits 59, 60,
75, 76 and 82 added, 77-79 renamed. Levels: 一级/二级 -> fault, 三级 -> warning,
四级 and unlevelled -> info (the bits V2.7.1.1 adds have no level there). Reserved
and unlisted bits are reported as unrecognised, never dropped.
"""

ALARM_BASE = 0x75A5
ALARM_BITS = 128

# bit: (level, Chinese name, English name); level f = fault, w = warning, i = info.
_ALARMS = {
    0: ("w", "电池欠压告警", "Battery undervoltage"),
    1: ("f", "电池放电截止", "Battery end of discharge"),
    2: ("f", "电池平均过流", "Battery average overcurrent"),
    3: ("f", "电池硬件过流", "Battery hardware overcurrent"),
    4: ("w", "电池未接", "Battery not connected"),
    5: ("f", "电池过压", "Battery overvoltage"),
    6: ("w", "电池SOC低告警", "Battery level low"),
    7: ("f", "电池SOC低关机", "Battery level low, shut down"),
    8: ("f", "旁路过载", "Bypass overload"),
    9: ("f", "逆变过载", "Inverter overload"),
    10: ("f", "逆变输出短路", "Inverter output short circuit"),
    11: ("f", "逆变硬件过流", "Inverter hardware overcurrent"),
    12: ("f", "逆变电压直流分量异常", "Inverter DC component abnormal"),
    13: ("f", "母线硬件过压", "Bus hardware overvoltage"),
    14: ("f", "母线软件过压", "Bus software overvoltage"),
    15: ("f", "母线软件欠压", "Bus undervoltage"),
    16: ("f", "母线短路", "Bus short circuit"),
    17: ("f", "PV过压", "PV overvoltage"),
    18: ("f", "PV软件过流", "PV software overcurrent"),
    19: ("f", "PV硬件过流", "PV hardware overcurrent"),
    20: ("f", "绝缘阻抗低", "Low insulation resistance"),
    21: ("f", "PV散热器过温", "PV heat sink overtemperature"),
    22: ("f", "逆变散热器过温", "Inverter heat sink overtemperature"),
    23: ("f", "变压器过温", "Transformer overtemperature"),
    24: ("f", "输入继电器短路", "Input relay stuck"),
    25: ("f", "输出继电器短路", "Output relay stuck"),
    26: ("f", "风扇故障", "Fan fault"),
    27: ("f", "EEPROM故障", "EEPROM fault"),
    28: ("f", "主从SPI通讯故障", "Internal SPI communication fault"),
    29: ("f", "机型错误", "Model setting error"),
    30: ("f", "母线软起失败", "Bus soft start failed"),
    31: ("f", "漏电流过大", "Leakage current too high"),
    32: ("f", "并机控制CAN故障", "Parallel CAN fault"),
    33: ("w", "AFCI通讯故障", "AFCI communication fault"),
    34: ("f", "并机模式设置错误", "Parallel mode setting error"),
    35: ("f", "并机均流故障", "Parallel current sharing fault"),
    36: ("w", "并机地址错误", "Parallel address error"),
    37: ("f", "并机电池电压差异大", "Parallel battery voltage mismatch"),
    38: ("w", "并机市电输入不一致", "Parallel grid input mismatch"),
    39: ("f", "并机硬件同步故障", "Parallel hardware sync fault"),
    40: ("f", "并机固件版本不一致", "Parallel firmware mismatch"),
    41: ("f", "并机接线故障", "Parallel wiring fault"),
    42: ("f", "序列号错误", "Serial number error"),
    43: ("f", "直流侧异常引起系统过载", "System overload from DC side"),
    44: ("w", "辅助DSP请求关波", "Auxiliary DSP requested stop"),
    45: ("f", "正负母线不平衡", "Bus imbalance"),
    46: ("f", "外部CT主机设置错误", "External CT host setting error"),
    47: ("f", "市电输入相位错误", "Grid input phase error"),
    48: ("i", "BMS通讯故障", "BMS communication fault"),
    49: ("i", "BMS其他告警", "BMS other alarm"),
    50: ("f", "BMS电池高温", "BMS battery high temperature"),
    51: ("f", "BMS电池过流", "BMS battery overcurrent"),
    52: ("i", "BMS电池过压", "BMS battery overvoltage"),
    53: ("f", "BMS电池欠压", "BMS battery undervoltage"),
    54: ("f", "BMS电池低温", "BMS battery low temperature"),
    55: ("i", "PD板通讯故障", "PD board communication fault"),
    56: ("i", "PD板告警", "PD board alarm"),
    57: ("i", "BMS并包数量异常", "BMS pack count abnormal"),
    58: ("i", "BMS升级", "BMS updating"),
    59: ("i", "BMS系统故障", "BMS system fault"),
    60: ("i", "BMS加热异常", "BMS heating abnormal"),
    64: ("i", "电网过压", "Grid overvoltage"),
    65: ("i", "电网欠压", "Grid undervoltage"),
    66: ("i", "电网过频", "Grid overfrequency"),
    67: ("i", "电网欠频", "Grid underfrequency"),
    68: ("i", "电网10分钟过压", "Grid 10-minute overvoltage"),
    69: ("i", "低电压穿越异常", "Low voltage ride-through abnormal"),
    70: ("i", "高电压穿越异常", "High voltage ride-through abnormal"),
    71: ("i", "系统接地异常", "System grounding abnormal"),
    72: ("i", "直流拉弧", "DC arc"),
    73: ("i", "孤岛保护", "Anti-islanding protection"),
    74: ("f", "电表通讯异常", "Meter communication fault"),
    75: ("i", "微逆并网功率超限", "Micro-inverter grid power over limit"),
    76: ("i", "市电输入开路", "Grid input open"),
    77: ("w", "并机发电机不一致", "Parallel generator mismatch"),
    78: ("w", "发电机输入异常", "Generator input abnormal"),
    79: ("f", "输出电流反灌保护", "Output current backfeed protection"),
    82: ("i", "ROCOF频率异常", "ROCOF frequency abnormal"),
}
SEVERITY = {"f": "fault", "w": "warning", "i": "info"}
# Bits shown on the flow display as a missing battery / grid.
BATTERY_MISSING_BITS = {4}


def describe_alarm(profile, function, unit, bit):
    """The tracker's description of one alarm bit (`bit` = 0..127)."""
    result = {
        "alarm_id": f"{profile}:u{unit}:F{bit + 1:03}",
        "source": unit,
        "function": function,
        "code": f"{bit:04X}",
        "fault_code": f"F{bit + 1:02}",
        "name": "未识别异常",
        "name_en": "Unrecognized alarm",
        "severity": "unknown",
        "evidence_page": None,
        "applicable_types": [],
    }
    if bit in _ALARMS:
        level, zh, en = _ALARMS[bit]
        result.update(name=zh, name_en=en, severity=SEVERITY[level])
    return result


def alarm_bits(words):
    """128 booleans from the 8 alarm registers (bit 0 = register 0x75A5 bit 0)."""
    return [bool(words[n // 16] >> (n % 16) & 1) for n in range(ALARM_BITS)]
