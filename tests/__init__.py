"""Load the HA-independent protocol without importing the HA entry point."""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "energylib"
package = types.ModuleType("energylib_under_test")
package.__path__ = [str(INTEGRATION)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "energylib_under_test.protocol",
    INTEGRATION / "protocol" / "__init__.py",
    submodule_search_locations=[str(INTEGRATION / "protocol")],
)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)
