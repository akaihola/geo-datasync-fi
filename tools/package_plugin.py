"""Build a distributable plugin zip for upload via Plugins > Add > Upload plugin.

The archive contains the plugin manifest and every component directory:

    geo-datasync-fi/
      .claude-plugin/plugin.json
      skills/fi-open-geodata/...

By default the contents sit inside one top-level folder named after the plugin,
which mirrors how .skill bundles are laid out. If the uploader wants the manifest
at the archive root instead, pass --flat.

Standard library only, so a clean checkout can build the bundle with nothing
installed.

    python tools/package_plugin.py            # -> dist/geo-datasync-fi.zip
    python tools/package_plugin.py --flat
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".claude-plugin/plugin.json"

#: Everything a plugin can legitimately ship. Anything else in the repo - tests,
#: tooling, the virtualenv - is development scaffolding and stays out.
COMPONENT_DIRS = (
    "skills", "agents", "commands", "hooks", "workflows", "output-styles", "themes"
)

EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", "evals", "work", "out", "dist"}
EXCLUDE_NAMES = {".DS_Store", ".geocode-cache.json"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_include(relative: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in relative.parts):
        return False
    if relative.name in EXCLUDE_NAMES:
        return False
    return relative.suffix not in EXCLUDE_SUFFIXES


def collect(root: Path) -> list[Path]:
    files = [root / ".claude-plugin/plugin.json"]
    for name in COMPONENT_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        files += [
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and should_include(path.relative_to(root))
        ]
    return files


def package(root: Path, out_dir: Path, flat: bool) -> Path:
    if not MANIFEST.is_file():
        raise SystemExit(f"no plugin manifest at {MANIFEST}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    name = manifest.get("name")
    if not name:
        raise SystemExit("plugin.json has no name field")

    files = collect(root)
    skills = sorted(
        p.parent.name for p in files if p.name == "SKILL.md"
    )
    if not skills:
        print("warning: the plugin ships no skills - is that intended?")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.zip"
    prefix = Path("") if flat else Path(name)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, prefix / path.relative_to(root))

    for path in files:
        print(f"  {path.relative_to(root)}")
    size_kb = target.stat().st_size / 1024
    print(f"\nplugin: {name} {manifest.get('version', '(unversioned)')}")
    print(f"skills: {', '.join(skills) or 'none'}")
    print(f"{len(files)} files -> {target} ({size_kb:.0f} KB)"
          + (" [flat]" if flat else f" [wrapped in {name}/]"))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--flat",
        action="store_true",
        help="put .claude-plugin/ at the archive root instead of inside a folder",
    )
    args = parser.parse_args()
    package(ROOT, args.out_dir.resolve(), args.flat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
