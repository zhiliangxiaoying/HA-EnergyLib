"""Digital twin packaging: bundled files, manifest and idempotent panel registration."""

import ast
import importlib
import json
import re
import struct
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "energylib"
TWIN = COMPONENT / "twin"


def glb_json(path):
    data = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    assert magic == 0x46546C67 and chunk_type == 0x4E4F534A, path
    return json.loads(data[20 : 20 + chunk_length])


@dataclass(frozen=True)
class StaticPathConfigDouble:
    url_path: str
    path: str
    cache_headers: bool = True


class HttpDouble:
    """aiohttp refuses a second GET route for the same path; mirror that."""

    def __init__(self, fail=False):
        self.paths, self.fail = [], fail

    async def async_register_static_paths(self, configs):
        if self.fail:
            raise RuntimeError("http not ready")
        for config in configs:
            if any(p.url_path == config.url_path for p in self.paths):
                raise RuntimeError("route already registered")
            self.paths.append(config)


def load_panel_module(panels):
    def register_panel(hass, component_name, *, update=False, **kwargs):
        path = kwargs["frontend_url_path"]
        if path in panels and not update:
            raise ValueError(f"Overwriting panel {path}")
        panels[path] = {"component_name": component_name, **kwargs}

    async def get_integration(hass, domain):
        return SimpleNamespace(version="9.8.7")

    names = [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.frontend",
        "homeassistant.components.http",
        "homeassistant.core",
        "homeassistant.loader",
    ]
    modules = {name: ModuleType(name) for name in names}
    modules["homeassistant.components.frontend"].async_register_built_in_panel = register_panel
    modules["homeassistant.components.http"].StaticPathConfig = StaticPathConfigDouble
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.loader"].async_get_integration = get_integration
    sys.modules.pop("energylib_under_test.twin_panel", None)
    with patch.dict(sys.modules, modules):
        return importlib.import_module("energylib_under_test.twin_panel")


class TwinFilesTests(unittest.TestCase):
    def test_page_scripts_and_models_are_bundled(self):
        page = (TWIN / "index.html").read_text(encoding="utf-8")
        script = (TWIN / "twin.js").read_text(encoding="utf-8")
        files = re.findall(r"'((?:vendor/)?[\w.]+\.js)'", page)
        self.assertIn("twin.js", files)
        self.assertIn("vendor/three.min.js", files)
        for name in files:
            self.assertTrue((TWIN / name).is_file(), name)
        models = re.findall(r"load\('([\w-]+\.glb)'", script)
        self.assertEqual(len(models), 5)
        # The loading screen waits for every model.
        self.assertIn(f"loaded.size < {len(models)}", script)
        for name in models:
            self.assertTrue((TWIN / "models" / name).is_file(), name)
        total = sum(p.stat().st_size for p in TWIN.rglob("*") if p.is_file())
        self.assertLess(total, 4 * 1024 * 1024)

    def test_page_uses_no_cdn_or_unauthenticated_api(self):
        for name in ("index.html", "twin.js"):
            source = (TWIN / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"https?://", source), name)
            self.assertNotIn("fetch(", source)
            self.assertNotIn("/api/", source)
            self.assertNotIn("energy-config.json", source)

    def test_compressed_models_ship_their_decoder(self):
        page = (TWIN / "index.html").read_text(encoding="utf-8")
        for path in (TWIN / "models").glob("*.glb"):
            used = set(glb_json(path).get("extensionsUsed", []))
            if "EXT_meshopt_compression" in used:
                self.assertIn("vendor/meshopt_decoder.js", page)
            self.assertNotIn("KHR_draco_mesh_compression", used)

    def test_noncommercial_vehicle_model_is_removed(self):
        # The earlier CC BY-NC car must not come back in any form.
        vehicle = re.compile(r"EV_Generic_SUV|EV_Real_Generic_SUV|EV_badge_removed_", re.I)
        for path in (TWIN / "models").glob("*.glb"):
            gltf = glb_json(path)
            names = [n.get("name", "") for n in gltf.get("nodes", []) + gltf.get("meshes", [])]
            self.assertFalse([n for n in names if vehicle.search(n)], path.name)
        for path in TWIN.rglob("*"):
            if path.is_file() and path.suffix in (".html", ".js", ".md", ".glb"):
                text = path.read_bytes().decode("latin-1").lower()
                for word in ("tesla", "model y", "bloxbloger"):
                    self.assertNotIn(word, text, path.name)
        self.assertFalse(list(TWIN.rglob("MODEL-CREDITS*")))

    def test_sports_car_has_no_brand_parts_and_is_credited(self):
        gltf = glb_json(TWIN / "models" / "sports-car.glb")
        parts = [
            item.get("name", "")
            for key in ("nodes", "meshes", "materials")
            for item in gltf.get(key, [])
        ]
        brand = re.compile(r"emblem|badge|logo|license ?plate|corvette|chevrolet", re.I)
        self.assertFalse([p for p in parts if brand.search(p)])
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        for credit in (
            "Martin Trafas",
            "CC BY 4.0",
            "sports-car.glb",
            "2b509d1bce104224b147c81757f6f43a",
        ):
            self.assertIn(credit, notice)

    def test_notice_credits_bundled_libraries(self):
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("three.js r128", notice)
        self.assertIn("meshoptimizer", notice)
        self.assertIn("MIT License", notice)

    def test_manifest_dependencies_cover_panel_imports(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
        tree = ast.parse((COMPONENT / "twin_panel.py").read_text(encoding="utf-8"))
        imported = {
            node.module.split(".")[2]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("homeassistant.components.")
        }
        self.assertEqual(imported, {"frontend", "http"})
        self.assertTrue(imported <= set(manifest["dependencies"]))
        self.assertIn("zeroconf", manifest["dependencies"])

    def test_setup_registers_panel_and_yaml_is_entry_only(self):
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("await async_register_twin_panel(hass)", source)
        self.assertIn("CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)", source)


class TwinPanelRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.panels = {}
        self.module = load_panel_module(self.panels)

    async def test_repeated_registration_is_a_no_op(self):
        hass = SimpleNamespace(data={}, http=HttpDouble())
        await self.module.async_register_twin_panel(hass)
        await self.module.async_register_twin_panel(hass)
        self.assertEqual(len(hass.http.paths), 1)
        static = hass.http.paths[0]
        self.assertEqual(static.url_path, "/energylib_twin")
        self.assertEqual(Path(static.path), TWIN)
        panel = self.panels["energylib-twin"]
        self.assertEqual(panel["component_name"], "iframe")
        self.assertEqual(panel["sidebar_title"], "EnergyLIB 数字孪生")
        self.assertFalse(panel["require_admin"])
        self.assertEqual(panel["config"], {"url": "/energylib_twin/index.html?v=9.8.7"})

    async def test_failed_static_registration_can_be_retried(self):
        hass = SimpleNamespace(data={}, http=HttpDouble(fail=True))
        with self.assertRaises(RuntimeError):
            await self.module.async_register_twin_panel(hass)
        self.assertNotIn("energylib-twin", self.panels)
        hass.http.fail = False
        await self.module.async_register_twin_panel(hass)
        self.assertEqual(len(hass.http.paths), 1)
        self.assertIn("energylib-twin", self.panels)
