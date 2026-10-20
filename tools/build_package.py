"""Build a local component-only preview zip; never upload/publish it."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def build(output=None):
    root = Path(__file__).resolve().parents[1]
    component = root / "custom_components" / "energylib"
    version = json.loads((component / "manifest.json").read_text(encoding="utf-8"))["version"]
    target = Path(output) if output else root / "dist" / f"energylib-{version}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(component.rglob("*")):
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".json", ".yaml", ".png", ".md", ".html", ".js", ".glb"):
                continue
            name = "energylib/" + path.relative_to(component).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
            count += 1
    return {
        "path": str(target),
        "version": version,
        "files": count,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
