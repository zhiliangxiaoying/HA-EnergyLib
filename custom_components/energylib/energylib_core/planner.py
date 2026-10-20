"""Merge known adjacent mappings, never undocumented holes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadBlock:
    function: int
    unit: int
    address: int
    count: int
    mappings: tuple


def plan_reads(mappings, *, register_limit=2, bit_limit=202):
    blocks = []
    for m in sorted(set(mappings), key=lambda p: (p.function, p.unit, p.address)):
        maximum = bit_limit if m.function == 2 else register_limit
        if m.count > maximum:
            raise ValueError("Mapping exceeds profile block limit")
        if blocks:
            old = blocks[-1]
            end = max(old.address + old.count, m.address + m.count)
            if (
                m.function == old.function
                and m.unit == old.unit
                and m.address <= old.address + old.count
                and end - old.address <= maximum
            ):
                blocks[-1] = ReadBlock(
                    old.function, old.unit, old.address, end - old.address, old.mappings + (m,)
                )
                continue
        blocks.append(ReadBlock(m.function, m.unit, m.address, m.count, (m,)))
    return blocks
