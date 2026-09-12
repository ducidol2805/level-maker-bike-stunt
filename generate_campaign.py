#!/usr/bin/env python3
"""Materialize campaign maps from the versioned obstacle catalog."""
import argparse
import json
from pathlib import Path
from obstacle_library import build_campaign_level


def main():
    parser = argparse.ArgumentParser(description="Generate up to 50 maps using authored obstacle variants.")
    parser.add_argument("--output", type=Path, default=Path("levels"))
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace existing generated files")
    args = parser.parse_args()
    if not 1 <= args.count <= 50:
        parser.error("--count must be between 1 and 50")
    paths = [args.output / f"campaign_{n:02d}.json" for n in range(1, args.count+1)]
    manifest_path = args.output / "campaign_manifest.json"
    existing = [p for p in paths+[manifest_path] if p.exists()]
    if existing and not args.overwrite:
        parser.error(f"{len(existing)} destination files exist; choose a new output or use --overwrite")
    levels = [build_campaign_level(n) for n in range(1, args.count+1)]
    args.output.mkdir(parents=True, exist_ok=True)
    for path, level in zip(paths, levels):
        path.write_text(json.dumps(level, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"count": len(levels), "catalogVersion": "2.0",
                "validationStatus": "geometry_only; Unity playtests pending",
                "maps": [{"id": level["map"]["id"], "file": path.name,
                          "variants": level["design"]["variants"]}
                         for path, level in zip(paths, levels)]}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {len(levels)} catalog-based maps in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
