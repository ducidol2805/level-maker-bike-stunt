# Black Ridge Assault — design brief

`black_ridge_assault.json` is a complete 148-unit, medium-hard Bike Stunt level built from semantic terrain cross-sections. It starts with a forgiving speed build, peaks at the visible Quarry Gap, releases into suspension-recovery waves, then turns technical at Black Ridge before the final gap jump.

## Route sequence

```text
Start Pad → Quarry Hill → Compression Kicker → Quarry Gap (J01)
→ Flow Landing → Recovery Waves → Step Up → Technical Ridge
→ Final Kicker → Black Ridge Finale (J02) → Flow Finish
```

## Difficulty and player flow

| Moment | Intent | Protection |
| --- | --- | --- |
| J01 Quarry Gap | First major speed/air-control test. | Broad downhill flow landing, then 23 units before the next major jump. |
| Recovery Waves | Require posture control without breaking speed. | No immediate fatal edge. |
| Ridge step-up | Tests throttle modulation. | Checkpoint comes after J01 and before the finale. |
| J02 Finale | Highest-energy jump. | Landing is 8 units long and transitions into a finish release. |

The deadzone shapes are deliberately local: `quarry_gap_kill` and `final_gap_kill` trace only their gaps. They do not create a broad, invisible kill floor beneath the whole map. The map has exactly 10 optional coins: eight prioritize launch, flight, tabletop, recovery-wave, and ridge-arc performance lines; the remaining two reward the final launch and finish release.

## Unity handoff notes

- Union `MainPlatform` source polygons only after authoring; retain their IDs for level-design iteration.
- Preserve `drivingSurface` point-index metadata when converting outlines into SpriteShape/collider data.
- `RampPlatform` is open geometry and should not be automatically closed on import.
- Tune the `speedWindow` targets against the actual bike before declaring the level final. The JSON contains the recommended 80–120% validation sweep.
