"""Model effects and persistence are tested independently from the HA client."""

import unittest
from datetime import date, datetime

from energylib_under_test.protocol.discovery import parse_discovery

from tools.sinexcel_simulator import (
    BenchConfig,
    BenchModel,
    mdns_properties,
    mdns_service_info,
    tou_limits,
)


class BenchMdnsTests(unittest.TestCase):
    def test_advertisement_follows_discovery_contract(self):
        info = mdns_service_info("ELIB260002", "192.0.2.10", 15021)
        self.assertEqual(info.type, "_energylib._tcp.local.")
        self.assertEqual(info.port, 15021)
        props = mdns_properties("ELIB260002")
        discovery = parse_discovery("192.0.2.10", 15021, props)
        self.assertEqual(discovery.advertised_id, "ELIB260002")

    def test_loopback_bind_cannot_be_advertised(self):
        for bind in ("127.0.0.1", "0.0.0.0", "localhost"):
            with self.subTest(bind=bind), self.assertRaises(ValueError):
                mdns_service_info("ELIB-X", bind, 15021)


class BenchModelTests(unittest.TestCase):
    def test_export_limit_after_soc_clamps_charging_power(self):
        for soc in (94.999, 95):
            for limit in (0, 0.5):
                with self.subTest(soc=soc, limit=limit):
                    model = BenchModel(BenchConfig(writes=True))
                    model.base.soc = soc
                    for address, value in (
                        (0x322A, 1),
                        (0x3002, 1),
                        (0x3004, limit),
                        (0xC648, -2),
                        (0xC646, 1),
                    ):
                        model.set_float(address, value)
                    model.last_heartbeat = model.clock()
                    model.tick(datetime(2026, 10, 19, 12), 1)
                    values = model.base.values
                    self.assertGreaterEqual(values["grid_power"], -limit - 1e-6)
                    self.assertLessEqual(model.base.soc, 95)
                    self.assertAlmostEqual(
                        values["solar_power"] + values["battery_power"] + values["grid_power"],
                        values["load_power"],
                    )

    def test_tou_outside_mode_matches_standalone_mode(self):
        from tools.sinexcel_simulator import _sx, _tou

        for outside, work_mode in enumerate((0, 3, 5, 6)):
            for hour in (2, 12):
                with self.subTest(outside=outside, hour=hour):
                    now = datetime(2026, 10, 19, hour)
                    direct = BenchModel(BenchConfig(writes=True))
                    direct.set_float(0x300C, work_mode)
                    direct.tick(now, 1)
                    tou = BenchModel(BenchConfig(writes=True))
                    # Only Tuesday has an active period; Monday uses outside mode.
                    schedule = _tou.TouSchedule.parse(
                        {
                            "periods": [dict(type="charge", start="01:00", end="06:00", power=2)],
                            "weekdays": ["tue"],
                            "outside_mode": _tou.OUTSIDE_MODES[outside],
                        },
                        tou_limits(BenchConfig()),
                    )
                    for (address, _), words in zip(
                        _sx.SCHEDULE_BLOCKS, _sx.encode_schedule(schedule, date(2026, 10, 19))
                    ):
                        tou.apply_write(address, words)
                    tou.set_float(0x300C, 1)
                    tou.set_float(0x314E, outside)
                    tou.tick(now, 1)
                    self.assertEqual(tou.base.values, direct.base.values)

    def test_night_tou_grid_charge_requires_enable(self):
        model = BenchModel(BenchConfig(writes=True))
        from tools.sinexcel_simulator import _sx, _tou

        schedule = _tou.TouSchedule.parse(
            {"periods": [dict(type="charge", start="01:00", end="06:00", power=2)]},
            tou_limits(BenchConfig()),
        )
        for (address, _), words in zip(
            _sx.SCHEDULE_BLOCKS, _sx.encode_schedule(schedule, date(2026, 10, 19))
        ):
            self.assertEqual(model.apply_write(address, words), 0)
        model.set_float(0x300C, 1)
        model.tick(datetime(2026, 10, 19, 2), 1)
        self.assertEqual(model.base.values["battery_power"], 0)
        model.set_float(0x322A, 1)
        model.tick(datetime(2026, 10, 19, 2), 1)
        self.assertEqual(model.base.values["battery_power"], -2)
        v = model.base.values
        self.assertAlmostEqual(
            v["solar_power"] + v["battery_power"] + v["grid_power"], v["load_power"]
        )

    def test_export_limit_curtails_without_breaking_conservation(self):
        model = BenchModel(BenchConfig(writes=True))
        model.set_float(0x300C, 6)
        model.set_float(0x3002, 1)
        model.set_float(0x3004, 0)
        model.tick(datetime(2026, 10, 19, 12), 1)
        self.assertGreaterEqual(model.base.values["grid_power"], -1e-6)
        v = model.base.values
        self.assertAlmostEqual(
            v["solar_power"] + v["battery_power"] + v["grid_power"], v["load_power"]
        )

    def test_settings_persist_but_remote_lease_never_resumes(self):
        config = BenchConfig(writes=True)
        model = BenchModel(config)
        model.tick(datetime(2026, 10, 19, 12), 0)
        model.set_float(0x314A, 40)
        model.set_float(0xC646, 1)
        model.set_float(0xC648, 3)
        restored = BenchModel(config, state=model.state())
        self.assertEqual(restored.get_float(0x314A), 40)
        self.assertEqual(restored.get_float(0xC646), 0)
        self.assertEqual(restored.get_float(0xC648), 0)
