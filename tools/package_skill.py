"""Build a distributable .skill bundle from the skill directory.

A .skill file is a zip whose single top-level directory contains SKILL.md. That
is what claude.ai accepts as a skill upload, and it is also a perfectly good way
to hand the plugin to someone as one file.

Standard library only, so it works in a clean checkout with nothing installed.

    python tools/package_skill.py                 # -> dist/fi-open-geodata.skill
    python tools/package_skill.py --out-dir /tmp
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL = ROOT / ".claude/skills/fi-open-geodata"

# Build artefacts and local state that would otherwise ride along in the bundle.
EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", "evals", "work", "out"}
EXCLUDE_NAMES = {".DS_Store", ".geocode-cache.json"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_include(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    return path.suffix not in EXCLUDE_SUFFIXES


def package(skill_dir: Path, out_dir: Path) -> Path:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SystemExit(f"no SKILL.md in {skill_dir} - is that the right directory?")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{skill_dir.name}.skill"

    files = sorted(
        p for p in skill_dir.rglob("*") if p.is_file() and should_include(p.relative_to(skill_dir))
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            # Paths inside the archive are prefixed with the skill folder name,
            # which is what makes SKILL.md land one level down where the loader
            # expects it.
            archive.write(path, Path(skill_dir.name) / path.relative_to(skill_dir))

    for path in files:
        print(f"  {path.relative_to(skill_dir)}")
    size_kb = target.stat().st_size / 1024
    print(f"\n{len(files)} files -> {target} ({size_kb:.0f} KB)")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    package(args.skill_dir.resolve(), args.out_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
