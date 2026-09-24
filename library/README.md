# Library identifiers

Each variant in `types/*.json` has a unique positive integer `libID`. It is
persistent: changing a variant's name, parameters or position in the catalog
must not change its `libID`. The existing string `id` remains the recipe key.

After adding variants, assign IDs only to the new entries:

```powershell
python -m bike_stunt.library_ids
```

The catalog's `nextLibID` prevents reuse of removed IDs. Missing or duplicate
IDs are rejected when loading the library.

Map shape IDs use `s{stage:02d}_{libID}_{libName}`, where `libName` is the
original local shape ID, for example `s00_1_terrain`. All shapes in one obstacle
share its `libID`; their local names distinguish its terrain, ramps and hazards.
References are remapped during placement. Coins and other point objects retain
their existing instance IDs.

Merged terrain keeps the first shape's ID, all `sourceShapeIds`, and positioned
`sourceShapeLabels`. Map previews show these source names on their corresponding
sections even when point controls are hidden.

Ground bodies (`MainPlatform`) have at least 15 units of depth in the editor/map
(7.5 source units at world scale 2). Lower the bottom corners to meet this depth;
keep the driving surface unchanged. Curves below their knots count toward the
minimum depth. Thicker authored bodies remain unchanged.

Map composition and export do not align or offset bottom Y coordinates. Merging
retains the left shape's outer left bottom corner and the right shape's outer
right bottom corner at their original heights.


Platform-library closed ramp bodies merge adjacent vertices within 0.05 source
units after geometry overrides and before world scaling. The merged position is
the average rounded to 2 decimals; merged bottom corners are tagged so the
driving-surface count and library editor round-trip remain valid.
