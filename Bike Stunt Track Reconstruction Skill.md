# Skill: Bike Stunt Track Reconstruction

## Purpose

Analyze reference gameplay footage from a 2D / 2.5D side-view bike stunt game and reconstruct the playable track as structured level-design data.

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

Maintain two library groups, each with five authored variants:

- `spring_high`: strong elevation change, emphasizing an upward cinematic reveal.
- `spring_far`: long horizontal separation, emphasizing a sweeping flight.

Read [spring_high](library/types/spring_high.json) and
[spring_far](library/types/spring_far.json) when selecting variants; their
parameters are the source of truth. Use the SpringObject contract in this skill
when authoring launch/target fields or integrating the Unity trigger.

Use these on some maps as a cinematic highlight, not on every map and not in
back-to-back obstacle slots. The current campaign recipe uses one spring transfer
on every fifth map, alternating high/far; this cadence is configurable, not a
universal rule for every authored level.

Show the spring and destination before commitment, frame the flight apex, and
leave a broad catch plus stabilization space. Prefer coin rewards along the
spring flight while keeping exactly 20 optional coins in a complete map.
Place a checkpoint on the first stable post-catch pad, subject to the existing
End-distance rule. Do not move Start/End when adding or exporting this obstacle.

Validate the estimated arc clears the departure lip, the full gap and the
destination's leading wall, arrives descending over the catch, and never crosses
the deadzone. Translate the target with the spring during composition. Geometry
checks alone do not certify playability: trigger timing, full-bike clearance,
landing impact, rearming and camera behavior require Unity playtests. Isolated
library previews are not complete maps and need not contain 20 coins.

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

This is a C1-continuous join. There must be no micro-gap, overlap, or
unintentional change of direction at the terrain-to-connector or
connector-to-loop boundary.

For a loop, use a small number of deliberate Bezier control points that
describe the arc. Do not approximate it with dozens of short straight lines.

## Explosive Connector Lifecycle

Use explicit gameplay state:

```text
armed
-> player commits to the connector
-> player clears the exit join
-> destroyed
```

Do not destroy the collider when the front wheel first touches the connector.
Destroy only after the full bike has cleared the connector, such as when the
rear wheel or bike-center has passed the exit join. Otherwise the bike can
lose support mid-feature for an unintended failure.

After destruction, the connector creates an intentional break in the route.
Record the post-destruction route explicitly: a fallback ground line, a
one-way shortcut, or a checkpoint reset. Do not assume that deleting the
segment "creates a path" unless a separate revealed path has been authored.

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
- Coins: guide the intended flight/contact line and reward commitment. Allocate
  a budget per beat, with more coins on long cinematic arcs and fewer on short
  jumps or tight loops. Keep spacing readable and leave intentional empty space
  on restart pads, recovery and the final release.

Record beat ID, purpose, participating object IDs, coin budget, camera cue and
landing/recovery intent in the authored design. Mark assumed trajectories and
speeds as unvalidated until tested with the real bike. Do not promise that all
coins are collectible from static geometry alone.

---

# Coin Placement Policy

Every map must contain exactly **20 collectible coins** unless an explicit
level brief overrides that count. Coins are optional rewards; they must not be
required for the critical driving line or for finishing the map.

Place coins in this priority order:

```text
1. Stunt moments: loop apexes, jump apexes, trick-tabletops, high lines
2. Flight arcs that reward controlled air trajectory
3. Optional risk/reward routes and alternate ramp lines
4. Readable recovery or flow lines that guide the player forward
5. Remaining coins on safe, visually interesting terrain
```

Use coin arcs to communicate a desired stunt trajectory. For example:

```text
Launch
  -> coin arc through the safe high-flight line
  -> landing
```

Do not place a coin so that collecting it demands an impossible landing,
an untelegraphed blind trajectory, or a collision with a mandatory obstacle.
Avoid filling a narrow precision landing with many coins; one intentional coin
is enough. When a coin is placed over a dangerous gap, ensure missing it still
allows the player to take the safe completion route.

Record for each coin:

```text
position
route / stunt association
optional = true
collection risk level
```

Before export, verify both the exact count of 20 and that the strongest visual
stunt moments received coin placement before any filler placement.

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

---

# Start / End Platform Padding (Project Export Rule)

`Start` and `End` are gameplay markers, not the outer vertices of the terrain.
Keep their complete authored transforms unchanged when extending platforms,
merging terrain, exporting, or preparing a preview. Never regenerate these
markers from the expanded terrain bounds or rebase the map to remove negative X.

After composition, extend only the first `MainPlatform` 20 world units to the
left of its original outer edge and the last `MainPlatform` 20 world units to
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

For example, original terrain X bounds `[0, 307]` become `[-20, 327]`, while
Start `(1, 1)` and End `(306, -6.75)` remain at those exact world positions.
Use the existing `terrain_export.py` pipeline for this project.

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
