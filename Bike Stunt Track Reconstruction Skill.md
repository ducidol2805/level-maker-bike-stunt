# Skill: Bike Stunt Track Reconstruction

## Purpose

Analyze reference gameplay footage from a 2D / 2.5D side-view bike stunt game and reconstruct the playable track as structured level-design data.

Also use the project-specific sections below when maintaining this repository's
obstacle library, recipes, map generator and export pipeline. Reconstruction
from footage is a separate mode; ordinary library changes do not require a new
video, panorama or visual preview.

## Project application layout

- `apps/map_viewer/`: independent read-only map viewer; launch with `python -m apps.map_viewer`.
- `apps/library_studio/`: obstacle library preview and shape editor; launch with `python -m apps.library_studio`.
- `bike_stunt/`: shared geometry, campaign, export and UI implementation modules.
- Platform library ramp polygons merge adjacent points closer than 0.05 source units after overrides and before world scaling; the merged position is averaged and rounded to 2 decimals.
- `tests/`: automated tests. Keep every `.py` file, including tests and launchers, below 1,000 physical lines.
- Root scripts such as `level_visualizer.py`, `obstacle_library.py` and `generate_campaign.py` are compatibility launchers. New implementation belongs in the packages above.

## Project operating constraints

- Do not run ImageGen, generate preview images, render contact sheets, take
  screenshots or launch/open a visual preview unless the user explicitly asks.
  A supplied reference image authorizes reading that reference, not generating
  another image. Editing preview code is allowed within an implementation task;
  use geometry/data checks to verify it without launching the UI.
- Apply the latest user correction. The current coin rule is three coins owned
  by each library obstacle, replacing both the old 20-coin map budget and the
  intermediate two-coins-per-cinematic-point rule.
- Treat exact library counts, themes and campaign numbering below as this
  project's current brief. A later explicit brief can change them.
- In-memory builds and passing tests do not update saved maps. When asked to
  propagate library changes, regenerate the requested JSON files and manifest,
  then inspect those saved outputs before reporting completion.

The goal is not to copy every visible pixel or manually trace the terrain frame-by-frame.

The goal is to recover the **driving line**, classify the track into meaningful gameplay segments, estimate their geometry, and produce data that can later be converted into Unity SpriteShape splines, ramps, and gameplay objects.

---

# Core Principle

Treat the level as:

```text
A sequence of vehicle movement states
```

rather than:

```text
A continuous terrain image
```

A track should be understood as combinations of:

```text
Approach
→ Acceleration / Deceleration
→ Compression
→ Launch
→ Flight
→ Landing
→ Recovery
→ Next Challenge
```

Geometry exists to create these movement states.

---

# Scope

Use this skill for games similar to:

- Trials
- Hill Climb Racing
- Bike stunt games
- Side-view motocross games
- Physics-based vehicle platformers
- Momentum-based 2D vehicle games

The expected level representation consists primarily of:

- Main ground spline
- Ramp splines
- Platforms
- Drops
- Gaps
- Gameplay markers
- Checkpoints
- Obstacles
- Environmental props when relevant

---

# Inputs

Possible inputs include:

- Gameplay video
- Extracted video frames
- Screenshots
- Existing partial track data
- Vehicle physics configuration
- Known bike dimensions
- Known Unity world scale
- Reference SpriteShape geometry

Preferred information, when available:

```text
Vehicle wheelbase
Wheel radius
Maximum speed
Typical gameplay speed
Gravity
Camera zoom
Camera follow offset
```

Exact physical values are not required.

Relative measurements are acceptable as long as a consistent scale is maintained.

---

# Primary Output

Produce a structured track specification.

Example:

```json
{
  "segments": [
    {
      "type": "flat",
      "length": 12
    },
    {
      "type": "hill",
      "length": 10,
      "height": 3
    },
    {
      "type": "jump",
      "approachLength": 8,
      "launchAngle": 24,
      "launchHeight": 1.8,
      "gapWidth": 9,
      "landingAngle": -17,
      "landingLength": 8
    }
  ]
}
```

The final format may instead be JSON, YAML, SVG-like path data, or direct SpriteShape control points depending on the target pipeline.

---

# Reconstruction Workflow

## Step 1: Inspect the Entire Clip

Watch the reference from beginning to end before reconstructing geometry.

Identify:

- Overall track length
- Major terrain transitions
- Significant jumps
- Large elevation changes
- Camera behavior
- Checkpoint positions
- Repeated obstacle patterns
- Vehicle speed changes
- Sections where the player deliberately brakes or accelerates

Do not begin by tracing individual frames.

Create a rough semantic timeline first.

Example:

```text
00:00 Flat start
00:03 Rolling hills
00:07 Large downhill
00:10 High-speed kicker
00:12 Gap jump
00:14 Downhill landing
00:17 Recovery
00:20 Technical ramp section
00:28 Finish
```

---

# Step 2: Establish Scale

Choose one visually stable reference.

Preferred reference:

```text
Bike wheelbase
```

Alternative references:

```text
Wheel diameter
Bike body length
Known ramp size
Known gameplay object
```

Define:

```text
referencePixels → worldUnits
```

Example:

```text
Bike wheelbase on screen = 160 px
Assumed world wheelbase = 2 units

Scale ≈ 80 px / unit
```

The current catalog uses `worldScale: 2`. The example above calibrates the
unscaled source geometry; built Library previews and saved campaign maps use
twice those coordinates. This does not resize the Unity bike automatically.

Scale may vary because of camera zoom.

If camera zoom changes, treat scale as frame-dependent.

---

# Step 3: Determine Camera Motion

Do not assume screen coordinates are world coordinates.

Side-view games commonly use a moving camera.

Estimate:

```text
cameraX
cameraY
zoom
```

for keyframes whenever possible.

Use static terrain features across overlapping frames to estimate camera displacement.

If the bike remains near a fixed horizontal screen position:

```text
terrain screen displacement ≈ inverse camera displacement
```

Use overlapping terrain to align neighboring frames.

---

# Step 4: Build a World-Space Panorama

When possible, stabilize the video relative to the level.

Conceptually transform:

```text
Scrolling gameplay footage
```

into:

```text
One long world-space track image
```

Align overlapping frames using visible static terrain.

Prefer terrain features over the vehicle for frame alignment.

Useful anchors:

- Ramp edges
- Peaks
- Valleys
- Platforms
- Buildings
- Trees
- Rocks
- Static props

The panorama does not need to be visually perfect.

It only needs enough geometric consistency to reconstruct the driving line.

---

# Step 5: Extract the Driving Line

Trace only the surface relevant to bike movement.

Do not reproduce underground terrain shapes unless required by rendering.

Extract:

```text
Top collision surface
```

Example:

```text
             ______
           /
__________/
```

not:

```text
             ______
           /████████
__________/█████████
████████████████████
```

Focus on the path that interacts with the wheels.

---

# Step 6: Segment the Track

Break the driving line into semantic primitives.

Do not represent an entire level as one arbitrary Bézier spline.

Preferred segment vocabulary follows.

---

# Track Primitive Vocabulary

## Flat

```text
______________
```

Parameters:

```text
length
slope
```

---

## Rise

```text
          _____
        /
_______/
```

Parameters:

```text
length
height
entrySlope
exitSlope
```

---

## Fall

```text
______
      \
       \_____
```

Parameters:

```text
length
height
entrySlope
exitSlope
```

---

## Hill

```text
        ___
      /     \
_____/       \_____
```

Parameters:

```text
length
height
ascentLength
descentLength
curvature
```

---

## Valley

```text
_____           _____
     \_________/
```

Parameters:

```text
length
depth
entrySlope
exitSlope
curvature
```

---

## Wave

```text
____/\____/\____/\____
```

Parameters:

```text
amplitude
wavelength
count
```

---

## Kicker

```text
___________/
```

A short launch surface designed to produce airtime.

Parameters:

```text
length
height
launchAngle
curvature
```

---

## Curved Ramp

```text
            /
         __/
      __/
_____/
```

Parameters:

```text
length
height
entryAngle
launchAngle
curvature
```

---

## Tabletop

```text
        ________
_______/        \_______
```

Parameters:

```text
takeoffLength
topLength
height
landingLength
```

---

## Gap

```text
_______/

                  \_______
```

Parameters:

```text
gapWidth
gapHeight
```

A gap normally belongs to a larger Jump Unit.

---

## Step Up

```text
_______/          ________
                 /
```

Landing height is above takeoff height.

Parameters:

```text
horizontalDistance
heightDifference
```

---

## Step Down

```text
__________
           \
            \_______
```

Landing lies below takeoff.

Parameters:

```text
horizontalDistance
heightDifference
```

---

## Drop

```text
________
        |
        |
        \________
```

Parameters:

```text
dropHeight
landingOffset
landingShape
```

---

## Platform

```text
       ___________
______/           \______
```

or isolated:

```text
        ________
```

Parameters:

```text
length
height
entryType
exitType
```

---

# Library Variant Design (Project Brief)

`platform`, `spring_high` and `spring_far` currently have ten variants each.
Differentiate the geometry and the riding action: approach profile, height
sequence, crest/basin arrangement, launch, landing and recovery. Scaling the
same profile or renaming it does not provide the requested diversity.

Platform variants 1/5/8 currently correspond to `flat_bridge`, `wave_bridge`
and `double_hump`: keep their elevated surfaces disconnected. Reaching the next
piece should depend on sufficient momentum. Floating platforms are allowed;
do not fill their gaps or add supports just to make them touch the ground.
Other platform variants need not become disconnected automatically.

Use stable variant IDs in recipes, not UI indexes. Verify current ordering in
the family JSON when a user refers to a numbered card. Update catalog counts
when adding/removing variants and migrate recipes that reference deleted IDs.

---

# Spring Jump Obstacle (Cinematic Platform Transfer)

Treat the complete spring transfer as a reusable obstacle, not a SpringObject
scattered independently into the level:

```text
Left MainPlatform / approach
-> SpringObject at its right lip
-> open flight gap with a deadzone below
-> right MainPlatform / catch
-> recovery -> next obstacle
```

The assembly contains exactly two closed MainPlatform terrain shapes, one
SpringObject at the left platform's right edge, and a compact closed deadzone
covering the full horizontal gap with a small edge overlap. Keep the gap empty
of ground; do not add an invisible bridge or merge across it at export. Give
both terrain undersides the map's common bottom Y after composition.

Maintain two library groups, currently with ten authored variants each:

- `spring_high`: strong elevation change, emphasizing an upward cinematic reveal.
- `spring_far`: long horizontal separation, emphasizing a sweeping flight.

Read [spring_high](library/types/spring_high.json) and
[spring_far](library/types/spring_far.json) when selecting variants; their
parameters are the source of truth. Use the SpringObject contract in this skill
when authoring launch/target fields or integrating the Unity trigger.

Choose these for a named vertical reveal or wide crossing. Authored recipes
control their cadence and can contain several separated transfers. The fallback
procedural generator's every-fifth-map rule is not the authored campaign policy.
Keep recovery between major transfers unless a deliberate expert combination
has been requested.

Show the spring and destination before commitment, frame the flight apex, and
leave a broad catch plus stabilization space. The complete spring obstacle owns
three optional coins along its flight, using the shared library placement rule.
Place a checkpoint on the first stable post-catch pad, subject to the existing
End-distance rule. Preserve the composed Start/End during export; a requested
route extension may change the newly authored End position.

Validate the estimated arc clears the departure lip, the full gap and the
destination's leading wall, arrives descending over the catch, and never crosses
the deadzone. Translate the target with the spring during composition. Geometry
checks alone do not certify playability: trigger timing, full-bike clearance,
landing impact, rearming and camera behavior require Unity playtests. Isolated
library cards and composed maps both retain those same three local coin objects.

---

# Jump Unit

Do not analyze takeoff ramps independently.

Represent a jump as:

```text
Approach
→ Takeoff
→ Flight
→ Landing
→ Recovery
```

Recommended representation:

```json
{
  "type": "jump",
  "approach": {
    "length": 10,
    "slope": 2
  },
  "launch": {
    "angle": 27,
    "height": 2,
    "length": 4
  },
  "flight": {
    "gapWidth": 12,
    "heightDifference": -1
  },
  "landing": {
    "angle": -18,
    "length": 9
  },
  "recovery": {
    "length": 8
  }
}
```

---

# Speed Window

Every major obstacle should be evaluated using a speed window.

Define:

```text
minimumSpeed
idealSpeed
maximumSpeed
```

Example:

```text
< 8 m/s      → undershoot

8–15 m/s     → success

~11 m/s      → ideal trajectory

> 15 m/s     → overshoot
```

Representation:

```text
FAIL        SUCCESS             FAIL

──────|====================|────────
      8        11         15
               ↑
             ideal
```

Wider success ranges generally indicate easier obstacles.

Narrow success ranges indicate precision challenges.

---

# Difficulty Analysis

Do not estimate difficulty only from obstacle size.

Evaluate:

```text
Speed tolerance
Required acceleration
Required braking
Approach visibility
Landing width
Landing angle
Airtime
Reaction time
Recovery distance
Vehicle orientation requirement
Consequence of failure
```

A large jump with a wide landing can be easier than a small jump with a narrow speed window.

---

# Landing Analysis

Landing geometry is as important as takeoff geometry.

Determine:

```text
Landing position
Landing slope
Landing length
Trajectory alignment
```

For flow-oriented sections, the landing slope should approximately follow the direction of the descending vehicle trajectory.

Good flow:

```text
           .
        .
      .
   .
__/                 \
                     \
                      \____
```

Avoid accidental flat impacts:

```text
           .
       .
   .
__/                 __________
```

unless a hard impact is intentionally part of the challenge.

---

# Landing Categories

Classify major landings as one of:

```text
Safe
Flow
Precision
Hard
Trick
```

## Safe

Large tolerance.

Used after difficult sections.

## Flow

Maintains forward momentum.

Landing matches trajectory.

## Precision

Small landing zone or narrow speed tolerance.

## Hard

Designed to produce strong compression or impact.

## Trick

Provides large airtime and enough landing tolerance for rotations.

---

# Recovery Sections

After significant challenges, measure the amount of stabilization space.

Example:

```text
Big Jump
→ Landing
→ Recovery
→ Next Challenge
```

Recovery allows:

```text
Bike suspension stabilization
Rotation correction
Speed normalization
Player reaction
Camera recentering
```

Avoid unintentionally chaining multiple major challenges without recovery.

---

# Track Rhythm

Evaluate the level as a sequence of tension levels.

Example:

```text
Easy
Easy
Medium
Hard
Easy
Medium
Hard
Easy
```

Prefer alternating:

```text
Build
→ Challenge
→ Release
→ Build
→ Challenge
```

Avoid long sequences of maximum-intensity obstacles unless intentionally creating an expert section.

---

# Momentum Preservation

For flow-oriented sections, analyze whether consecutive geometry preserves velocity.

Evaluate:

```text
Entry velocity
Exit velocity
Vertical impact
Unexpected braking
Suspension compression
Terrain curvature
```

Smooth terrain transitions should avoid unnecessary velocity destruction.

Technical sections may deliberately break momentum.

---

# Camera Analysis

Camera design is part of track difficulty.

Measure approximate visible distance ahead of the bike.

Estimate:

```text
ReactionTime = VisibleDistance / VehicleSpeed
```

Example:

```text
VisibleDistance = 20 units
Speed = 10 units/s

ReactionTime = 2 s
```

At:

```text
20 units/s
```

the same camera provides:

```text
1 s
```

of reaction time.

Track reconstruction should therefore record whether major hazards are visible early enough.

---

# Blind Jumps

Identify jumps where the landing is outside the visible camera region at takeoff.

Classify them as:

```text
Intentional blind jump
or
Camera limitation
```

Do not reproduce accidental blind jumps blindly if the objective is to preserve gameplay quality rather than pixel-perfect level copying.

---

# Checkpoint Placement

Prefer checkpoints after challenge resolution.

Recommended:

```text
Challenge
→ Landing
→ Stabilization
→ Checkpoint
→ Next challenge
```

Avoid checkpoints:

- Mid-flight
- During unstable landings
- Immediately before unavoidable collisions
- In positions that restart the player with insufficient speed

Record:

```text
checkpoint position
expected restart speed
restart orientation
```

when available.

## Hard-Gap Checkpoint Rule

Every difficult gap jump should be evaluated for a checkpoint immediately
after its resolution:

```text
Hard gap
-> landing
-> short stabilization distance
-> checkpoint
```

Place the checkpoint at the first safe, rideable position after the landing,
normally after the suspension has settled but before the player is asked to
solve another meaningful challenge. Do not put it before the gap, mid-flight,
or on the landing face itself.

Do not place a checkpoint too close to `End`. A final-gap checkpoint is omitted
when the remaining finish route is shorter than a meaningful release section.
As a default guideline, keep the final checkpoint at least:

```text
max(8 world units, 8% of total track length)
```

from `End`, unless the target game has a documented different restart policy.
The finish should feel like a finish, not a checkpoint followed by a few
unavoidable meters.

---

# Ramp Separation

Treat constructed ramps separately from natural ground whenever the target implementation uses separate SpriteShapes or prefabs.

Example:

```json
{
  "ground": [...],
  "ramps": [
    {
      "type": "wood_kicker",
      "position": [32, 4],
      "angle": 25,
      "length": 4
    }
  ]
}
```

Do not bake every ramp into the main ground spline.

---

# Ramp Assemblies and Explosive Connectors

Treat a constructed stunt feature that is made of several pieces as one
**Ramp Assembly**, not as unrelated ramps and objects.

Example: an explosive connector leading into a loop.

```text
MainPlatform driving surface
  -> explosive connector (destroyable open curve)
  -> permanent loop body (open curve)
  -> loop exit / recovery route
```

The explosive connector is a load-bearing part of the route while it exists.
It is not merely an object placed near a ramp.

Recommended representation:

```json
{
  "id": "loop_01",
  "type": "ramp_assembly",
  "parts": [
    {
      "id": "loop_connector",
      "type": "explosive_ramp",
      "closed": false,
      "points": ["terrainJoin", "...", "loopJoin"],
      "state": "armed"
    },
    {
      "id": "loop_body",
      "type": "ramp",
      "closed": false,
      "points": ["loopJoin", "...", "loopExit"]
    }
  ]
}
```

At every assembly join:

```text
position A end == position B start
tangent A end is collinear with tangent B start
```

Opposite, collinear handles provide tangent-direction continuity (G1); exact
C1 additionally requires matching derivative magnitudes. There must be no micro-gap, overlap, or
unintentional change of direction at the terrain-to-connector or
connector-to-loop boundary.

For a loop, use a small number of deliberate Bezier control points that
describe the arc. Do not approximate it with dozens of short straight lines.

## Explosive Connector Lifecycle

The required player experience is:

```text
ride through -> explode behind the bike -> reveal a useful new route -> continue
```

Use `armed -> committed -> detonated -> route_revealed -> spent`. Do not destroy
the supporting connector on first contact. Follow rear-wheel progress on the
ring route; world X is not a valid progress metric on a path that turns back.
The current trigger is `rear_wheel_clears_route_progress` at normalized progress
0.42 of its own `pre_route`, after connector clearance.

Each connector reveals its own exit ramp. That ramp starts hidden with collision
disabled and becomes collidable with zero reveal delay. Keep debris visual-only
and restore the assembly on checkpoint reset. Record `revealsRoute` and
`postDestroyRoute` as real object references and prefix them when placing an
instance. A deletion without a useful authored continuation does not meet this
feature's gameplay intent.

## Current crossed-ramp ring family

The library exposes exactly `round`, `double_ring` and `triple_ring`; do not
restore the abandoned ten-variant family unless requested.

- The orange ring route is the upper semicircle, from the rightmost contact
  (0 degrees) over the top to the leftmost contact (180 degrees).
- The explosive ramp curves from the left ground entry to the right contact;
  the revealed ramp curves from the left contact down to the right ground exit.
  They cross below the ring, with symmetric tangents and horizontal outer ends.
- Keep the combined silhouette low and nearly circular. Avoid a tall teardrop
  or a pinched lower crossing. Entry/exit sit exactly one unit outside the
  ring's horizontal bounds.
- The currently accepted local settings are radius X/Y 4, ground clearance 0.5,
  boundary clearance 1, ground-handle ratio 0.64 and ring handle 4.5. At world
  scale 2, each ring spans 16 units; the two 2-unit outer gaps make 20 units per
  ring assembly. Read
  [explosive_loop.json](library/types/explosive_loop.json) before editing; these
  are editable design settings, not a universal physical formula.
- Variant 2 is two complete copies of variant 1; variant 3 is three copies.
  Each copy retains its own armed connector, active ring, hidden exit and
  trigger/reveal references. Join the previous reveal endpoint directly to the
  next connector start. Do not make one ring reveal or destroy the next ring.

For vertical-loop challenges, estimate the initial speed window before physics
simulation. A conservative contact-preserving lower bound at the bottom is:

```text
v_min approximately sqrt(5 * gravity * loopRadius)
```

Then validate the real assembly against the actual bike, drag, suspension, and
motor model. The loop top and exit must be visible before commitment unless
the design deliberately calls for a blind expert challenge.

---

# Gameplay Object Reconstruction

Record important objects in world space.

Examples:

```text
Checkpoint
Coin
Boost
Crate
Rock
Explosive
Bridge
Moving platform
Wind zone
Breakable object
Finish marker
```

For each object record:

```text
type
position
rotation
scale
variant
```

Example:

```json
{
  "type": "checkpoint",
  "x": 84.2,
  "y": 3.1
}
```

---

# Purposeful Cinematic Object Placement

Design cinematic objects as a sequence with a readable setup, commitment,
spectacle, landing/fallback and recovery. Name the beat each object serves;
do not distribute springs, boosts or explosives at uniform distances or merely
to satisfy a feature checklist. Reuse an assembly when its movement and camera
intent fit the route, not just because an empty stretch is available.

- Spring high: reveal a higher destination through vertical flight. Spring far:
  emphasize a wide crossing. Frame the destination and apex; clear the catch.
- Explosive ramp: create a deliberate one-use commitment into a stunt. Specify
  when the full bike clears, what route remains afterwards, and reset behavior.
- Speed boost: prepare or sustain the speed needed by a named stunt. Put it on
  the appropriate approach/branch, not arbitrarily on recovery or before End.
  Stack boosts only for an explicit purpose; test zero/one/two pickups. Do not
  place boosts just before a spring that replaces incoming velocity unless
  another documented gameplay purpose justifies them.
- Explosive barrel: a directed optional pop or transfer with a readable landing,
  clear recovery and a safe route for skipping it. Impulse plus entry velocity
  and bike mass determine its flight; do not treat it as a deterministic spring.
- Coins: carry the three library-owned rewards with each obstacle. Use the
  actual contact/flight route, with loop coins inset toward the ring center.
  Do not allocate a second cinematic budget or add map-level filler.

Record beat ID, purpose, participating object IDs, actual coin count, camera cue and
landing/recovery intent in the authored design. Mark assumed trajectories and
speeds as unvalidated until tested with the real bike. Do not promise that all
coins are collectible from static geometry alone.

---

# Coin Placement Policy

Every library obstacle owns exactly **three optional coin objects in local
coordinates**. This includes flat/start/finish variants and the whole double-
or triple-ring variant: three per obstacle, not three per component ring.

- Build coins through `assign_module_coins` in `bike_stunt/obstacle_library.py`, as native
  `InteractableObject` entries. Library cards and composed maps use the same
  objects. `place` prefixes their IDs and translates their coordinates.
- Map total = three times the number of obstacle instances, including authored
  start and finish modules. Do not restore a global 20-coin quota, a two-coin
  cinematic cap, or an independent map-level distribution pass.
- For a loop, handle the inside-ring route before a generic `rewardLine`.
  Shrink toward the actual translated center/radii; adding a fixed positive Y
  offset to the upper ring puts coins outside it. Keep `supportingRingId`
  valid after placement, including multiple-ring variants.
- Follow the spring trajectory for springs and the estimated flight for manual
  jumps. If a recipe changes a catch or adds a barrel flight, recompute the
  three local coins from the updated route before placing the module.
- Follow actual platform segments or the driving surface for terrain. Do not
  interpolate across disconnected platform gaps or along vertical ledge walls.
- The current builder uses quarter/half/three-quarter route positions measured
  by segment arc length, excluding jumps between disconnected paths. Validate
  separation and support instead of assuming these fractions always suffice.
- Record unique ID, local/world position, source variant, route association,
  `optional: true` and collection-risk status. Actual collectibility still
  requires a bike playtest.

Verify exactly three coins per variant and per placed instance; no duplicated
coins on assembly/export; unchanged coin count/coordinates on repeated export;
correct ID/reference translation; loop containment; and safe surface placement.
Update `design.coinCount`, per-beat counts and manifest-derived data from the
actual objects.

---

# Geometry Simplification

Raw tracing may contain hundreds of points.

Do not preserve unnecessary points.

Process:

```text
Raw traced polyline
↓
Noise removal
↓
Polyline simplification
↓
Curve fitting
↓
Semantic control points
```

Possible algorithms:

```text
Ramer-Douglas-Peucker
Bezier fitting
Catmull-Rom fitting
Spline resampling
```

Keep points where they affect:

```text
Slope
Curvature
Launch angle
Landing angle
Collision behavior
```

Remove points representing only visual noise.

---

# Curve Control Points and Unity SpriteShape Export

Do not store a gameplay curve as position-only vertices when it has a smooth
transition. Position-only polylines create accidental collision corners and
cannot faithfully reproduce the intended Unity SpriteShape curve.

Use explicit Bezier-style spline points for all non-linear driving surfaces:

```json
{
  "x": 32.0,
  "y": 4.5,
  "tangentIn": { "x": -2.0, "y": -0.3 },
  "tangentOut": { "x": 2.2, "y": 0.4 },
  "tangentMode": "continuous",
  "corner": false
}
```

`tangentIn` and `tangentOut` are local vectors relative to the control-point
position. They map directly to SpriteShape left/right tangent data.

Use these modes:

```text
linear       Intentional straight edge; no curve is generated.
continuous   Smooth hills, valleys, flow landings, loops, and tangent joins.
broken       A controlled non-symmetric curve; use only when its entry and exit
              curvatures intentionally differ.
```

Set `corner: true` only for an intentional gameplay corner such as a hard step,
a platform lip, or a deliberately sharp kicker edge. A point that merely looks
angular in the reference is not automatically a corner; preserve the driving
line with a continuous tangent whenever suspension and wheel contact should
remain smooth.

For a closed `MainPlatform` terrain cross-section:

```text
top / driving boundary     Bezier or linear according to collision intent
side and underside         usually linear, with explicit corner points
```

The importer should preserve this point contract:

```text
position       -> SpriteShape Spline position
tangentIn      -> left tangent
tangentOut     -> right tangent
tangentMode    -> SpriteShape tangent mode
corner          -> SpriteShape corner mode
closed shape    -> closed SpriteShape / polygon collider outline
open ramp       -> open SpriteShape spline
```

Unity SpriteShape exposes control-point position, left and right tangents,
tangent mode, and corner handling. Preserve those fields rather than reducing
the source back to a line segment during export.

## Curve Validation

For every curved jump, landing, loop, or terrain join, validate:

```text
No visible or collision seam at shared endpoints
Tangent continuity at flow joins
No unintended high-curvature pinch point
No collision corner on a nominally smooth landing
Closed terrain outline remains non-self-intersecting
Open ramps remain open after import
```

Test tangent-sensitive features with slow, typical, and high entry speeds.
Small spline errors are especially destructive at kicker lips, loop entries,
loop exits, and landing transitions.

## Post-merge straight/curve handles

At a smooth driving-surface point with one collapsed tangent and one active
curve tangent, extend the handle on the straight side so the two handles are
collinear and opposite. Retain the point position and the curved-side handle.

The new handle must lie on the adjacent straight segment. Check that both its
chord and its far-end handle are collinear; skip intentional corners, angled
ledges, nonstraight neighbors, zero-length edges and underside/closure points.
Use the active handle length capped to one third of the adjacent straight
segment; equal lengths are not required if the straight segment is short.
Set the qualifying point to `continuous` and keep re-export idempotent.

Apply this after terrain merge and boundary padding via
`extend_straight_join_handles` in `bike_stunt/terrain_export.py`. Verify both
straight-to-curve and curve-to-straight joins, short/sloped straights, retained
corners and an unchanged second export.

---

# Start / End Platform Padding (Project Export Rule)

`Start` and `End` are gameplay markers, not the outer vertices of the terrain.
Keep their complete authored transforms unchanged when adding boundary padding,
merging terrain, exporting, or preparing a preview. Never regenerate these
markers from the expanded terrain bounds or rebase the map to remove negative X.

After composition, extend only the first `MainPlatform` 20 source units to the
left of its original outer edge and the last `MainPlatform` 20 source units to
the right of its original outer edge. This ground padding prevents the map
from looking cut off; it does not move the spawn or finish or add another
gameplay challenge.

- Add flat surface segments at the existing endpoint heights. Preserve the
  original driving-surface positions and Bezier curves; do not stretch them.
- Move the corresponding outer bottom corners horizontally to close the
  extended terrain. Keep the common bottom Y unchanged.
- Keep all markers, checkpoints, coins, ramps and obstacle placements fixed.
  Do not count padding as extra Start-to-End gameplay distance.
- Merge touching MainPlatform shapes at export, keeping gaps separate and
  preserving Bezier data. Padding must not accumulate on repeated exports.
- Compare Start/End transforms with the authoring source after export and
  after copying to the preview folder; they must match exactly.

For example, original source terrain X bounds `[0, 307]` become `[-20, 327]`,
while source Start `(1, 1)` and End `(306, -6.75)` remain unchanged during
export. With the current 2x output scale, the saved bounds are `[-40, 654]`,
Start `(2, 2)` and End `(612, -13.5)`.
Use the existing `bike_stunt/terrain_export.py` pipeline for this project.

This export rule does not prohibit moving End when the user requests a longer
authored map. Recompose the route first, then preserve its newly authored
markers during export.

---

# SpriteShape Conversion Guidelines

When generating Unity SpriteShape geometry:

Use control points primarily at:

```text
Slope transitions
Peaks
Valleys
Ramp starts
Ramp ends
Curvature extrema
Gameplay-critical landing areas
```

Do not create one control point for every sampled video pixel.

Prefer stable smooth curves.

---

# Validation

After reconstructing geometry, test using the game's actual vehicle physics.

Run the track using several entry speeds.

Example:

```text
80% typical speed
90%
100%
110%
120%
```

For major jumps record:

```text
Success
Undershoot
Overshoot
Landing impact
Landing position
Exit speed
```

---

# Track Analyzer

When simulation capabilities are available, automatically evaluate every major stunt.

Suggested output:

```text
Segment 14: Gap Jump

Entry speed:
8.2–14.8 m/s

Ideal:
11.1 m/s

Success window:
6.6 m/s

Airtime:
1.42 s

Landing impact:
Low

Landing tolerance:
High

Recovery distance:
9.3 m

Estimated difficulty:
Medium
```

---

# Segment Data Model

Every semantic segment should support four groups of parameters.

## Geometry

```text
Length
HeightDifference
EntrySlope
ExitSlope
Curvature
```

## Physics

```text
EntrySpeed
ExitSpeed
MinimumSpeed
IdealSpeed
MaximumSpeed
```

## Gameplay

```text
Difficulty
Airtime
TrickPotential
RecoveryTime
FailureType
```

## Camera

```text
LookAheadDistance
VisibleLanding
ReactionTime
VerticalVisibility
```

---

# Reconstruction Priority

When information is incomplete, prioritize accuracy in this order:

```text
1. Gameplay function
2. Driving line
3. Ramp geometry
4. Landing geometry
5. Relative distances
6. Elevation
7. Gameplay object placement
8. Decorative geometry
```

Do not sacrifice gameplay correctness in order to reproduce irrelevant visual details.

---

# Approximation Rules

Video reconstruction is inherently imperfect.

When exact geometry cannot be measured:

Prefer:

```text
Simple semantic geometry
```

over:

```text
Complex speculative geometry
```

Example:

If a hill appears approximately smooth:

Use:

```text
Hill
height = ~3
length = ~12
```

rather than generating 30 uncertain spline points.

Clearly mark estimated values.

---

# Library-to-Campaign Workflow (This Repository)

## Sources of truth

- `library/types/*.json`: obstacle variants and shape parameters.
  Authored `geometryOverrides` are authoritative, including the curved ramp
  shapes tuned from real data. Tests must preserve these points, handles and
  tangent modes rather than enforce the procedural builder's default profile.
- `library/obstacle_catalog.json`: family files, version and variant counts.
- `bike_stunt/obstacle_library.py`: local builders, three-coin ownership, placement and
  fallback procedural composition.
- `library/campaign_recipes.json`: authored map sequence, intent, tension,
  checkpoints, overrides and length targets. Check this first when updating
  existing campaign maps; do not reverse-engineer merged terrain unnecessarily.
- `library/campaign_themes.json`: theme materials, palette and scenery metadata.
  Metadata is not proof that decorative game assets have been implemented.
- `bike_stunt/campaign.py`: author/tune, place, export, validate and save maps.
- `bike_stunt/terrain_export.py`: merge all touching/overlapping MainPlatform ground,
  including side contact and containment; never use global bottom height as a merge gate.
  Keep exposed Bezier curves, source labels, reference remapping and stable boundary padding.
  `bike_stunt/terrain_union.py` handles intersecting outlines when endpoint splicing is insufficient.
  Collapse generated union edges below 0.05 source units before saving; validate adjacent
  spline spacing (including closed seams) to catch Unity import failures such as maps 17/29.
- `levels/`: generated maps and `campaign_manifest.json`.

Read current files before applying a stored convention. The mutable catalog
and recipes are authoritative for current IDs, counts, overrides and baselines.

## Propagating changes

When asked to update maps from the library, identify the existing targets and
their recipes, then use the shared builders. Preserve recipe-specific geometry,
coin ownership, checkpoint/reset references and theme settings. Do not manually
patch every translated copy of an obstacle in generated JSON.

For the current 30-map campaign, the regeneration command is:

```powershell
python -m bike_stunt.campaign --output levels --count 30 --overwrite
```

These commands write files; inspect destinations and choose the requested
count before executing them.
They do not launch previews. A standalone skill/document update does not
authorize regenerating maps.

Validate saved outputs and manifest against recipes, not just an in-memory
build. Compare hashes for out-of-scope maps if the generator rewrites the whole
set. Preserve unrelated edits and deletions. Do not recreate atlases, screenshots
or archives as a side effect of updating map JSON.

## Uniformly scaling an authored map

The current `library/obstacle_catalog.json` sets `worldScale: 2`. Keep type
parameters, geometry overrides and campaign recipes in source units. Apply
the factor once when building a Library preview, and once after authoring,
export and static validation when writing each campaign map. Do not scale a
Library module before recipe composition or scale an already saved map again.

The 2x output scale multiplies every spatial quantity by 2: spline point
positions and local `tangentIn`/`tangentOut`
vectors; Start, End, Top, Bottom, checkpoints, coins and other object positions;
target/landing/trigger/trajectory positions; terrain padding; obstacle ports;
camera look-ahead, framing bounds and trigger positions; route X/Y ranges; and
stored length, gap, recovery and spacing values. `world_scale.py` owns these
field rules. Library editor point changes are converted back to source units
on save, so reopening a variant does not double its override. Re-exporting a
saved map reuses its recorded 40-unit padding; new source exports start at 20
units before the 2x output scale.

Keep counts, IDs, rotations/angles, tangent modes, normalized progress,
direction vectors, multipliers, difficulty and event delays unchanged. Physical
speed, impulse and flight time use sqrt(2) for similar ballistic motion under
unchanged gravity. This transforms stored point-mass estimates; it is not a
Unity physics validation. Recompute estimates if the bike or gravity changes,
and validate in Unity. Verify closed terrain, joins, coins and metadata after
saving.

## Campaign themes and difficulty

Current authored coverage is maps 01-30. Maps 11-20 are beach; maps 21-30 are
desert. Beach themes use shore rhythms, piers, coves, cliffs and islands; desert
themes use dunes, canyon transfers, oasis basins and ruin steps. Choose distinct
obstacle sequences and riding actions as well as names/palettes. Carry theme and
setting through saved maps and manifest. This is the current campaign brief,
not a rule that all future batches must use these themes.

For the current campaign, numbers ending in 3/5/7/0 have been extended to 1.5
times their recorded original Start-to-End lengths. Endings 5/7 have harder
obstacles; 10/20/30 are `extreme`. Ending 3 requests length without an automatic
difficulty increase.

- Treat `lengthPlan.baselineLength` as the frozen original baseline and
  `targetLength` as the minimum regeneration length; longer maps are allowed.
  Preserve the finish release length when library obstacles grow. Never shorten
  it just to match the target or multiply the extended output again on refresh.
- Measure gameplay length as `End.x - Start.x`, excluding export padding.
- Add purposeful challenges and recovery to lengthen a map; do not stretch
  every X coordinate or satisfy most of the increase with a long empty flat.
- Raise difficulty through actual higher-tier variants, technical ledges,
  precision transfers and multi-loop sequences. Changing the difficulty label
  alone does not implement the request. Preserve readable landings/recovery.
- Record overrides in the recipe, keep three coins per added obstacle and
  verify the resulting marker distance against the target.

## Library and main-app UI conventions

When editing `bike_stunt/map_viewer/browser.py`, preserve Refresh/F5 in the map
browser. In Library Studio, keep the library refresh action available. Refresh
rereads current files/catalog; it is not a substitute for regenerating stale map JSON.

Library cells retain their background rectangle, have no cell outline, and use
75% of the earlier cell height. Group titles have no filled title rectangle;
preserve the existing separate group separator. These are settled UI preferences,
not authorization to launch a preview during an unrelated task.

## Verification and reporting

Use the existing test suite and generator checks without rendering images:

```powershell
python -m unittest discover -s tests -v
git diff --check
```

Read the test runner's current discovered count instead of repeating a count
from this conversation. Check relevant contracts: three coins per instance,
inside-ring coin placement, ID remapping, independent explosive/reveal cycles,
smooth merge joins, unchanged repeat export, recipe/theme coverage and target
lengths. Authored jump catches must pass the existing 90/100/110 percent
point-mass speed checks. When a newly selected variant fails, tune its catch
with a recipe `landingLength` override and rerun the check; do not weaken the
validator merely to ship the recipe.

Report separately what was saved, what static checks passed, and any actual
Unity playtesting. Static geometry or point-mass tests do not prove bike
reachability, visual reference matching or an "extreme" difficulty rating.

---

# Agent Behavior

The agent should:

1. Inspect the full reference before drawing.
2. Establish a consistent scale.
3. Account for camera motion.
4. Segment the level into semantic primitives.
5. Reconstruct the driving line.
6. Separate ramps from terrain.
7. Group jumps as complete Jump Units.
8. Estimate relevant speed windows.
9. Analyze landing geometry.
10. Preserve gameplay rhythm.
11. Record checkpoints and gameplay objects.
12. Simplify spline geometry.
13. Export structured track data.
14. Validate against actual vehicle physics when possible.
15. Report uncertainties instead of inventing precision.

---

# Do Not

Do not:

- Trace every video frame independently.
- Treat screen coordinates as world coordinates.
- Copy underground visual terrain unnecessarily.
- Generate hundreds of spline points without simplification.
- Analyze ramps without their approach and landing.
- Judge difficulty only from jump distance.
- Ignore camera visibility.
- Ignore entry velocity.
- Merge constructed ramps into terrain when the implementation separates them.
- Assume exact measurements when the video does not support them.
- Optimize visual matching before validating gameplay.

---

# Recommended End-to-End Pipeline

```text
Gameplay Video
↓
Inspect full level
↓
Select keyframes
↓
Estimate scale
↓
Estimate camera transform
↓
Align overlapping frames
↓
Create world-space panorama
↓
Extract driving line
↓
Classify track primitives
↓
Measure primitive parameters
↓
Identify Jump Units
↓
Place ramps / checkpoints / objects
↓
Simplify geometry
↓
Export structured map
↓
Generate Unity SpriteShapes
↓
Run vehicle simulation
↓
Analyze speed windows
↓
Correct geometry
↓
Finalize track
```

---

# Final Deliverables

For every reconstructed level provide:

## Track Overview

```text
Length
Total elevation range
Number of major jumps
Number of technical sections
Estimated difficulty
```

## Segment Sequence

Example:

```text
Flat
→ Rise
→ Hill
→ Valley
→ Kicker
→ Gap Jump
→ Flow Landing
→ Recovery
→ Wave
→ Step Up
→ Technical Ramp
→ Finish
```

## Structured Track Data

JSON, YAML, or the target level format.

## Uncertainty Report

Example:

```text
Segment 6 launch angle:
Medium confidence

Segment 9 landing length:
Low confidence because landing was outside camera view.

Track scale:
Estimated from bike wheelbase.
```

## Validation Report

When simulation is available:

```text
Reachability
Speed windows
Overshoot / undershoot cases
Landing quality
Recovery quality
Potential impossible sections
```

---

# Design Principle Summary

The reconstructed level should preserve:

```text
Flow
Momentum
Challenge sequence
Jump trajectory
Landing behavior
Player reaction time
```

before attempting to preserve decorative visual details.

The primary question is not:

```text
"Does the curve look exactly like the video?"
```

The primary question is:

```text
"Does riding this reconstructed track reproduce the same movement, timing, and challenge?"
```
