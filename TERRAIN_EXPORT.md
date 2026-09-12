# Terrain finalization

After composition, both underside vertices of every MainPlatform use the lowest
existing underside Y across the map. Driving surfaces and the median-based
`Bottom` marker are unchanged. Catalog source shapes remain local and reusable.

Export a composed map for Unity:

```powershell
python terrain_export.py levels/00_cinder_crown.json --output exports/00_cinder_crown.json
```

The export merges endpoint-connected shapes, removes internal walls, retains
Bezier handles and closes each resulting landmass with two bottom vertices.
Gaps remain separate shapes. The default join tolerance is 0.000001 world units;
`--merge-tolerance` explicitly permits snapping a larger authoring mismatch.
Overlapping shapes are not polygon-unioned. Unsupported closure layouts are
rejected rather than guessing which points to move.

Merged shapes retain the first shape ID, record `metadata.sourceShapeIds` and
update explosive-ramp `postDestroyRoute` references. Use `--overwrite` to replace
an existing export; exporting over the authoring source is disallowed.
Campaign generation now applies the same export finalization automatically.

Exports also extend the first platform 20 units left and the last platform
20 units right, as flat ground at their existing endpoint heights. Start/End,
coins, checkpoints and original road curves stay in place. Both bottom corners
follow the new outer edges. `terrainExport.boundaryPadding` prevents repeated
extension when an exported file is exported again.
