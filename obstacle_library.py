"""Data-driven obstacle variants, local Bezier geometry, and preview export.

All physical numbers are design hypotheses, not results from Unity playtests.
The source of each variant is library/types/*.json, not a hard-coded map recipe.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
from pathlib import Path
from statistics import median
from terrain_export import normalize_main_platform, surface_count
from spring_object import spring_trajectory

ROOT = Path(__file__).resolve().parent
GROUPS = ("MainPlatform", "RampPlatform", "deadzone", "InteractableObject")


def catalog_version():
    return json.loads((ROOT / "library/obstacle_catalog.json").read_text(encoding="utf-8"))["version"]


def load_catalog():
    catalog = json.loads((ROOT / "library/obstacle_catalog.json").read_text(encoding="utf-8"))
    families = {}
    actual_total = 0
    for group in catalog["groups"]:
        family = json.loads((ROOT / "library" / group["path"]).read_text(encoding="utf-8"))
        actual_count = len(family["variants"])
        if group.get("variantCount") != actual_count:
            raise ValueError(f"{family['type']}: catalog declares {group.get('variantCount')} variants, found {actual_count}")
        families[family["type"]] = family
        actual_total += actual_count
    if catalog.get("variantCount") != actual_total:
        raise ValueError(f"Catalog declares {catalog.get('variantCount')} variants, found {actual_total}")
    return families


def vertex(x, y, incoming=(0, 0), outgoing=(0, 0), mode="broken"):
    return {"x": round(x, 6), "y": round(y, 6),
            "tangentIn": {"x": round(incoming[0], 6), "y": round(incoming[1], 6)},
            "tangentOut": {"x": round(outgoing[0], 6), "y": round(outgoing[1], 6)},
            "tangentMode": mode, "corner": False}


def profile(coords, slopes=None):
    """Monotone cubic Hermite -> Bezier; zero derivative at local extrema.

    Smooth outer ports use horizontal travel tangents. Broken handles at outer
    terrain vertices isolate curved road edges from linear underside edges.
    """
    slopes = slopes or {}
    result = []
    for i, (x, y) in enumerate(coords):
        a = (coords[i][1] - coords[i-1][1]) / (coords[i][0] - coords[i-1][0]) if i else 0
        b = (coords[i+1][1] - y) / (coords[i+1][0] - x) if i+1 < len(coords) else 0
        slope = slopes.get(i, 2*a*b/(a+b) if a*b > 0 else 0)
        left = (x-coords[i-1][0])/3 if i else 0
        right = (coords[i+1][0]-x)/3 if i+1 < len(coords) else 0
        result.append(vertex(x, y, (-left, -left*slope), (right, right*slope),
                             "continuous" if 0 < i < len(coords)-1 else "broken"))
    return result


def sample_curve(shape, steps=24):
    """Evaluate cubic Bezier edges; linear mode ignores tangent handles."""
    points = shape["points"]
    result = [(points[0]["x"], points[0]["y"])]
    pairs = list(zip(points, points[1:]))
    if shape.get("closed"):
        pairs.append((points[-1], points[0]))
    for a, b in pairs:
        out = a.get("tangentOut", {}) if a.get("tangentMode") != "linear" else {}
        inc = b.get("tangentIn", {}) if b.get("tangentMode") != "linear" else {}
        p0, p3 = (a["x"], a["y"]), (b["x"], b["y"])
        p1 = (p0[0]+out.get("x", 0), p0[1]+out.get("y", 0))
        p2 = (p3[0]+inc.get("x", 0), p3[1]+inc.get("y", 0))
        for n in range(1, steps+1):
            t = n/steps
            result.append(tuple((1-t)**3*p0[k]+3*(1-t)**2*t*p1[k]+3*(1-t)*t*t*p2[k]+t**3*p3[k] for k in (0,1)))
    return result


def ground(name, surface):
    floor = min(p["y"] for p in surface)-3
    points = copy.deepcopy(surface)
    # Closure handles must not bulge beyond the terrain footprint.
    points[0]["tangentIn"] = {"x": 0, "y": 0}
    points[-1]["tangentOut"] = {"x": 0, "y": 0}
    points[0]["tangentMode"] = points[-1]["tangentMode"] = "broken"
    points += [vertex(surface[-1]["x"], floor, mode="linear"), vertex(surface[0]["x"], floor, mode="linear")]
    return {"id": name, "closed": True, "points": points,
            "metadata": {"drivingSurfaceCount": len(surface)}}


def linear_polygon(name, coords):
    return {"id": name, "closed": True, "points": [vertex(x,y,mode="linear") for x,y in coords]}


def assign_module_coins(module):
    """Own three local-space rewards per obstacle, including library cards.

    Reapply after a recipe changes a catch/flight. Placement only translates
    these objects; map assembly never adds or redistributes rewards.
    """
    routes = []
    role = 'obstacle_surface'
    if module['type'] == 'explosive_loop':
        role = 'loop_inside_arc'
        for ring in module['RampPlatform']:
            if ring.get('metadata', {}).get('routePhase') != 'before_explosion':
                continue
            points = ring['points']
            cx = (points[0]['x']+points[-1]['x'])/2
            cy = (points[0]['y']+points[-1]['y'])/2
            rx = abs(points[0]['x']-points[-1]['x'])/2
            ry = max(p['y'] for p in points)-cy
            routes.append(([(cx+(x-cx)*(rx-.85)/rx, cy+(y-cy)*(ry-.85)/ry)
                            for x,y in sample_curve(ring,120)],ring['id']))
    elif 'rewardLine' in module:
        role = 'estimated_flight'
        routes = [(module['rewardLine'],None)]
    elif module['type'].startswith('spring_'):
        role = 'spring_flight'
        spring = next(o for o in module['InteractableObject'] if o['type']=='SpringObject')
        routes = [(spring_trajectory(spring,160),None)]
    elif module['jumpUnit']:
        role = 'estimated_flight'
        routes = [([(p['x'],p['y']) for p in module['coinCandidates']],None)]
    elif module['RampPlatform']:
        role = 'obstacle_platform'
        routes = [([(x,y+1) for x,y in sample_curve(shape,120)],None)
                  for shape in module['RampPlatform']]
    else:
        routes = [([(x,y+1.2) for x,y in sample_curve(
                    {'points':shape['points'][:surface_count(shape)]},120)],None)
                  for shape in module['MainPlatform']]
    # Accumulate only traversable segments: gaps between platforms/rings do
    # not become imaginary straight coin paths.
    edges, distances = [], [0.0]
    for line, ring_id in routes:
        for a,b in zip(line,line[1:]):
            if role == 'obstacle_surface' and abs(b[0]-a[0]) < 1e-8:
                continue
            length = math.dist(a,b)
            if length > 1e-9:
                edges.append((a,b,ring_id))
                distances.append(distances[-1]+length)
    if not edges:
        raise ValueError(f"{module['id']}: missing route for obstacle coins")
    coins = []
    for i,fraction in enumerate((.25,.5,.75),1):
        target = distances[-1]*fraction
        index = min(len(edges)-1,bisect.bisect_right(distances,target)-1)
        a,b,ring_id = edges[index]
        t = (target-distances[index])/(distances[index+1]-distances[index])
        x,y = (a[k]+t*(b[k]-a[k]) for k in (0,1))
        props = {'optional':True,'placement':'library_obstacle','line':role,
                 'sourceVariant':module['id'],'collectionRisk':'pending_playtest'}
        if ring_id:
            props['supportingRingId'] = ring_id
        coins.append({'id':f'coin_{i:02d}','type':'coin',
                      'transform':{'x':x,'y':y},'properties':props})
    module['InteractableObject'] = [o for o in module['InteractableObject'] if o['type']!='coin']+coins
    module['coinCandidates'] = [dict(c['transform'],priority=1,role=role) for c in coins]


def build_variant(family, variant):
    p = variant["parameters"]
    kind = family["builder"]
    type_id = family["type"]
    result = {key: [] for key in GROUPS}
    anchors, surfaces, checkpoints, joins = [], [], [], []
    physics = {"status": "unvalidated", "requiredBikeSkills": [family["primarySkill"]],
               "entrySpeedWindow": None, "exitSpeedWindow": None}
    units = None

    def add_surface(name, coords, slopes=None):
        surface = profile(coords, slopes)
        result["MainPlatform"].append(ground(name, surface))
        surfaces.append(surface)
        return surface

    if kind == "surface":
        coords = p["surface"]
        surface = add_surface("terrain", coords)
        end = coords[-1]
        peak = max(coords, key=lambda pt: pt[1])
        anchors.append({"x": peak[0], "y": peak[1]+1.2, "priority": 2, "role": "crest_or_flow"})
    elif kind == "table":
        a, b, c, h = p["up"], p["top"], p["down"], p["height"]
        coords = [(0,0),(8,0),(8+a,h),(8+a+b,h),(8+a+b+c,0),(18+a+b+c,0)]
        add_surface("rollable_table", coords)
        end = coords[-1]
        anchors.append({"x": 8+a+b/2,"y":h+1.2,"priority":4,"role":"tabletop"})
    elif kind == "technical":
        coords = [(0,0)]
        x, y = 8, 0
        for height in p["heights"]:
            coords += [(x,y),(x,y+height)]
            y += height
            x += p["spacing"]
        coords += [(x+p["shelf"],y)]
        surface = [vertex(x,y,mode="linear") for x,y in coords]
        result["MainPlatform"].append(ground("intentional_ledges",surface))
        surfaces.append(surface)
        end = coords[-1]
        anchors.append({"x": end[0]-p["shelf"]/2,"y":end[1]+1.2,"priority":3,"role":"ledge_clear"})
        physics["requiredBikeSkills"] += ["bunny_hop"]
    elif kind == "platform":
        length, h, mode = p["length"],p["height"],p["profile"]
        broken_segments = p.get("segments")
        if broken_segments:
            total_length = p.get("totalLength")
            if not isinstance(total_length, (int, float)) or total_length <= 0:
                raise ValueError(f"{variant['id']}: broken platform needs a positive totalLength")
            previous_end = None
            gaps = []
            add_surface("safe_fallback", [(0, 0), (total_length, 0)])
            for index, coords in enumerate(broken_segments, 1):
                if len(coords) < 2 or any(len(point) != 2 for point in coords):
                    raise ValueError(f"{variant['id']}: segment {index} needs at least two [x, y] points")
                if any(a[0] >= b[0] for a, b in zip(coords, coords[1:])):
                    raise ValueError(f"{variant['id']}: segment {index} must run strictly left to right")
                if previous_end is not None:
                    gap = coords[0][0]-previous_end
                    if gap <= 0:
                        raise ValueError(f"{variant['id']}: broken platform segments must not touch")
                    gaps.append([previous_end, coords[0][0]])
                previous_end = coords[-1][0]
                result["RampPlatform"].append({
                    "id": f"open_bridge_{index}", "closed": False, "points": profile(coords),
                    "metadata": {"floatingSegment": index > 1, "requiresMomentumTransfer": index < len(broken_segments)}})
                anchor = max(coords, key=lambda point: point[1])
                anchors.append({"x": anchor[0], "y": anchor[1]+1.2, "priority": 5,
                                "role": "broken_platform_transfer"})
            if broken_segments[0][0][0] != 0 or broken_segments[-1][-1][0] != total_length:
                raise ValueError(f"{variant['id']}: broken route must span x=0 through totalLength")
            end = (total_length, 0)
            physics["requiredBikeSkills"] += ["momentum_control", "air_control"]
            physics["brokenPlatformGaps"] = gaps
            physics["failureMode"] = "Insufficient launch speed drops the bike below the next floating platform"
        else:
            # Both ends connect to ground; elevated body is an open ramp. Entry
            # and runout lengths are variant parameters so bridges do not all
            # share the same silhouette even when their profiles differ.
            approach = p.get("approach", 8)
            runout = p.get("runout", 12)
            shape_profiles = {"flat":[(0,h),(length,h)],
                "arch":[(0,h),(length/2,h+2),(length,h)],
                "rise":[(0,h),(length*.7,h+2),(length,h+2)],
                "fall":[(0,h+2),(length*.3,h+2),(length,h)],
                "wave":[(0,h),(length*.25,h+1.4),(length*.5,h),(length*.75,h+1.4),(length,h)],
                "crown":[(0,h),(length*.18,h+.5),(length*.5,h+3.2),(length*.82,h+.5),(length,h)],
                "sag":[(0,h+1.8),(length*.22,h+.8),(length*.5,h),(length*.78,h+.8),(length,h+1.8)],
                "double_hump":[(0,h),(length*.22,h+2.4),(length*.5,h+.35),(length*.78,h+2.4),(length,h)],
                "switchback":[(0,h),(length*.2,h+2.8),(length*.46,h+.6),(length*.68,h+3.5),(length,h+1.1)],
                "skyline":[(0,h),(length*.16,h+1.8),(length*.34,h+1.8),(length*.5,h+3.8),
                           (length*.66,h+3.8),(length*.84,h+.7),(length,h+.7)]}
            if mode not in shape_profiles:
                raise ValueError(f"Unknown platform profile: {mode}")
            middle = [(x+approach,y) for x,y in shape_profiles[mode]]
            end_x = approach+length+runout
            coords = [(0,0),(approach*.5,0)]+middle+[(approach+length+runout*.5,0),(end_x,0)]
            add_surface("safe_fallback", [(0,0),(end_x,0)])
            ramp = {"id":"open_bridge","closed":False,"points":profile(coords)}
            result["RampPlatform"].append(ramp)
            end = (end_x,0)
            anchors.append({"x":middle[len(middle)//2][0],"y":max(y for x,y in middle)+1.2,"priority":4,"role":"alternate_high_line"})
    elif kind == "jump":
        a, length, h, angle = p["approach"],p["launch"],p["height"],math.radians(p["angle"])
        takeoff_x = a+length
        landing_x, landing_y = takeoff_x+p["gap"],h+p["delta"]
        # These families all produce a flight, but their ground topology is
        # deliberate: a kicker ends in a hard lip, a scoop compresses before
        # release, a gap uses a takeoff bank, and steps lead to a shelf at a
        # different elevation.  Do not reduce them to one generic ramp.
        if type_id == "kicker":
            coords = [(0, 0), (a, 0), (takeoff_x-length*.22, h*.34), (takeoff_x, h)]
            slopes = {2: math.tan(angle), 3: math.tan(angle)}
        elif type_id == "curved_ramp":
            compression = min(1.4, h*.32)
            coords = [(0, 0), (a*.68, 0), (a+length*.18, -compression),
                      (a+length*.62, h*.42), (takeoff_x, h)]
            slopes = {1: 0, 2: 0, 3: math.tan(angle)*.72, 4: math.tan(angle)}
        elif type_id == "gap":
            coords = [(0, 0), (a*.55, 0), (a+length*.28, h*.16), (takeoff_x, h)]
            slopes = {2: math.tan(angle)*.48, 3: math.tan(angle)}
        elif type_id == "step_up":
            coords = [(0, 0), (a, 0), (a+length*.38, h*.22), (takeoff_x, h)]
            slopes = {2: math.tan(angle)*.62, 3: math.tan(angle)}
        elif type_id == "step_down":
            coords = [(0, 0), (a*.78, 0), (a+length*.45, h*.28), (takeoff_x, h)]
            slopes = {2: math.tan(angle)*.58, 3: math.tan(angle)}
        else:
            coords = [(0,0),(a,0)] + ([(takeoff_x,h)] if length else [])
            slopes = {len(coords)-1:math.tan(angle)}
        launch_surface = add_surface("launch", coords, slopes)
        if type_id == "kicker":
            launch_surface[-1]["corner"] = True
            launch_surface[-1]["tangentMode"] = "broken"
            # add_surface has already copied the driving line into the closed
            # terrain shape, so preserve the authored lip in that output too.
            result["MainPlatform"][-1]["points"][len(launch_surface)-1]["corner"] = True
            result["MainPlatform"][-1]["points"][len(launch_surface)-1]["tangentMode"] = "broken"
        # Solve a point-mass trajectory to a target on the catch. This is only
        # an initial estimate; wheel radius, torque and suspension are absent.
        distance = p["gap"]+p["landing"]*.2
        target_y = landing_y-.3*p["landing"]*.2
        denom = 2*math.cos(angle)**2*(distance*math.tan(angle)-(target_y-h))
        ideal = math.sqrt(9.81*distance**2/denom)
        incoming_slope = math.tan(angle)-9.81*p["gap"]/(ideal*math.cos(angle))**2
        landing_end_y = landing_y-min(3,max(.4,abs(incoming_slope)*p["landing"]*.4))
        # Limit the endpoint derivative to a monotone catch. Otherwise a long
        # tangent could dip below recovery height and create an accidental bowl.
        incoming_slope = max(incoming_slope, 3*(landing_end_y-landing_y)/p["landing"])
        catch_end_x = landing_x+p["landing"]
        end = (catch_end_x+p["recovery"],landing_end_y)
        if type_id == "step_up":
            shelf_y = landing_y
            shelf_start = landing_x+p["landing"]*.35
            add_surface("elevated_shelf", [(landing_x, landing_y), (shelf_start, shelf_y),
                                             (catch_end_x, shelf_y), end], {0: incoming_slope, 1: 0, 2: 0})
        elif type_id == "step_down":
            drop_end = landing_x+p["landing"]*.38
            add_surface("descending_shelf", [(landing_x, landing_y), (drop_end, landing_end_y),
                                               (catch_end_x, landing_end_y), end], {0: incoming_slope, 1: 0, 2: 0})
        elif type_id == "curved_ramp":
            catch_mid_x = landing_x+p["landing"]*.42
            catch_mid_y = landing_y + (landing_end_y-landing_y)*.72
            add_surface("scoop_catch", [(landing_x, landing_y), (catch_mid_x, catch_mid_y),
                                         (catch_end_x, landing_end_y), end], {0: incoming_slope})
        else:
            add_surface("landing_recovery",[(landing_x,landing_y),(catch_end_x,landing_end_y),end],{0:incoming_slope})
        # A compact strip below the lip and landing catches failed crossings.
        kill_y = min(h,landing_y)-1.5
        result["deadzone"].append(linear_polygon("gap_kill",[(takeoff_x-.1,kill_y),(landing_x+.1,kill_y),(landing_x+.1,kill_y-1.5),(takeoff_x-.1,kill_y-1.5)]))
        for fraction in (.25,.5,.75):
            dx = p["gap"]*fraction
            cy = h+dx*math.tan(angle)-9.81*dx*dx/(2*(ideal*math.cos(angle))**2)
            anchors.append({"x":takeoff_x+dx,"y":cy+1,"priority":6,"role":"estimated_flight"})
        result['rewardLine'] = [(takeoff_x+dx,h+1+dx*math.tan(angle)-
                                9.81*dx*dx/(2*(ideal*math.cos(angle))**2))
                               for dx in (p['gap']*i/100 for i in range(101))]
        physics["entrySpeedWindow"] = {"minimum":round(ideal*.9,2),"ideal":round(ideal,2),"maximum":round(ideal*1.1,2),"units":"units/s","status":"target_not_verified"}
        units = {"approach":[0,a],"launch":[a,takeoff_x],"flight":[takeoff_x,landing_x],"landing":[landing_x,catch_end_x],"recovery":[catch_end_x,end[0]],"launchAngle":p["angle"],"landingCategory":"precision" if p["landing"]<=6 else "flow","checkpointCandidateAfterStabilization":catch_end_x+3}
        if variant["difficulty"] >= 4:
            checkpoints.append({"x":catch_end_x+3,"y":landing_end_y+1,"expectedRestartSpeed":0,"requiresRestartTest":True})
    elif kind == "spring_jump":
        lip, gap, height = p['approach'], p['gap'], p['height']
        landing_x = lip+gap
        catch_end = landing_x+p['landing']
        end_x = catch_end+p['recovery']
        approach_profile = p.get('approachProfile', 'flat')
        approach_depth = p.get('approachDepth', 1.5)
        launch_offset = p.get('launchOffset', 0)
        approach_profiles = {
            'flat': [(0, 0), (lip, launch_offset)],
            'dip': [(0, 0), (lip*.28, 0), (lip*.58, -approach_depth), (lip*.82, -.35),
                    (lip, launch_offset)],
            'crest': [(0, 0), (lip*.32, 0), (lip*.62, approach_depth), (lip*.84, .35),
                      (lip, launch_offset)],
            'ramp': [(0, 0), (lip*.3, 0), (lip*.72, launch_offset*.45), (lip, launch_offset)],
            'rollers': [(0, 0), (lip*.24, approach_depth*.7), (lip*.48, 0),
                        (lip*.72, approach_depth), (lip, launch_offset)]}
        if approach_profile not in approach_profiles:
            raise ValueError(f"Unknown spring approach profile: {approach_profile}")
        approach_coords = approach_profiles[approach_profile]
        launch_y = approach_coords[-1][1]
        landing_y = launch_y+height
        landing_profile = p.get('landingProfile', 'flat')
        landing_relief = p.get('landingRelief', 2)
        safe_target_end = min(p['landing']*.55, max(p['targetInset']+1, p['landing']*.3))
        landing_profiles = {
            'flat': [(landing_x, landing_y), (catch_end, landing_y), (end_x, landing_y)],
            'downslope': [(landing_x, landing_y), (landing_x+safe_target_end, landing_y),
                          (catch_end, landing_y-landing_relief), (end_x, landing_y-landing_relief)],
            'upslope': [(landing_x, landing_y), (landing_x+safe_target_end, landing_y),
                        (catch_end, landing_y+landing_relief), (end_x, landing_y+landing_relief)],
            'crown': [(landing_x, landing_y), (landing_x+safe_target_end, landing_y),
                      (landing_x+p['landing']*.68, landing_y+landing_relief),
                      (catch_end, landing_y), (end_x, landing_y)],
            'bowl': [(landing_x, landing_y), (landing_x+safe_target_end, landing_y),
                     (landing_x+p['landing']*.7, landing_y-landing_relief),
                     (catch_end, landing_y), (end_x, landing_y)],
            'rollers': [(landing_x, landing_y), (landing_x+safe_target_end, landing_y),
                        (landing_x+p['landing']*.62, landing_y+landing_relief),
                        (landing_x+p['landing']*.82, landing_y),
                        (catch_end, landing_y+landing_relief*.65),
                        (end_x, landing_y+landing_relief*.65)]}
        if landing_profile not in landing_profiles:
            raise ValueError(f"Unknown spring landing profile: {landing_profile}")
        landing_coords = landing_profiles[landing_profile]
        end = (end_x, landing_coords[-1][1])
        add_surface('spring_approach', approach_coords)
        add_surface('spring_landing_recovery', landing_coords)
        target_x = landing_x+p['targetInset']
        clearance = p['referenceClearance']
        distance = target_x-lip
        # Set a gentle descending arrival slope at the target. The broad flat
        # catch is intentional; suspension/contact still need a Unity test.
        duration = math.sqrt(2*(height-p['arrivalSlope']*distance)/9.81)
        spring = {'id': 'spring', 'type': 'SpringObject',
            'transform': {'x': lip, 'y': launch_y+clearance, 'rotation': 0},
            'properties': {'targetPosition': {'x': target_x, 'y': landing_y+clearance},
                'flightTimeSeconds': duration, 'gravity': 9.81, 'launchMode': 'target_arc',
                'activation': 'on_player_enter', 'rearm': 'on_exit', 'cinematic': True}}
        result['InteractableObject'].append(spring)
        result['deadzone'].append(linear_polygon('spring_gap_kill',
            [(lip-.1,min(launch_y,landing_y)-1.5),(landing_x+.1,min(launch_y,landing_y)-1.5),
             (landing_x+.1,min(launch_y,landing_y)-3),(lip-.1,min(launch_y,landing_y)-3)]))
        arc = spring_trajectory(spring)
        for fraction in (.15, .325, .5, .675, .85):
            x, y = arc[round(fraction*(len(arc)-1))]
            anchors.append({'x':x, 'y':y, 'priority':7, 'role':'cinematic_spring_flight'})
        units = {'approach':[0,lip], 'launch':[lip,lip], 'flight':[lip,landing_x],
            'landing':[landing_x,catch_end], 'recovery':[catch_end,end[0]],
            'launchAngle': math.degrees(math.atan2(height+.5*9.81*duration**2, distance)),
            'landingCategory':'safe', 'checkpointCandidateAfterStabilization':catch_end+3,
            'launchMechanism':'SpringObject'}
        checkpoints.append({'x':catch_end+3,'y':end[1]+clearance,
                            'expectedRestartSpeed':0,'requiresRestartTest':True})
        physics['springLaunch'] = {'status':'idealized_target_arc_only',
            'flightTimeSeconds':duration, 'arrivalSlope':p['arrivalSlope'],
            'entrySpeedPolicy':'incoming velocity replaced on trigger entry',
            'landingImpact':'broad flat catch; suspension and landing impact pending Unity test'}
    elif kind == "loop":
        layout = p.get("layout", "round_loop")
        approach = p["approach"]

        def ellipse_arc(rx, ry, entry_angle, exit_angle, segments=8, x_offset=0):
            cx = approach+x_offset+rx
            cy = ry+p.get("ringGroundClearance",2)
            angles = [math.radians(entry_angle+(exit_angle-entry_angle)*i/segments)
                      for i in range(segments+1)]
            arc = []
            for i, angle in enumerate(angles):
                factor = 4/3*math.tan((angles[1]-angles[0])/4)
                tangent = (-rx*math.sin(angle)*factor, ry*math.cos(angle)*factor)
                arc.append(vertex(cx+rx*math.cos(angle), cy+ry*math.sin(angle),
                                  (-tangent[0],-tangent[1]) if i else (0,0),
                                  tangent if i < segments else (0,0)))
            return arc

        ellipse_layouts = ("round_loop", "spiral_exit", "broken_halo",
                           "ring_chain_2", "ring_chain_3", "ring_chain_4")
        if layout in ellipse_layouts:
            points = ellipse_arc(p["radiusX"], p["radiusY"], p["entryAngle"],
                                 p["exitAngle"], 10 if layout == "spiral_exit" else 8)
        else:
            width, height = p["routeLength"], p["routeHeight"]
            route_profiles = {
                "blast_arch": [(approach,2),(approach+width*.18,3.5),(approach+width*.5,height),
                               (approach+width*.8,height*.55),(approach+width,3)],
                "tower_rollover": [(approach,2),(approach+width*.16,height*.68),
                                   (approach+width*.32,height),(approach+width*.48,height*.9),
                                   (approach+width*.68,height*.3),(approach+width,3)],
                "double_crown": [(approach,2),(approach+width*.2,height*.82),
                                 (approach+width*.43,height*.3),(approach+width*.68,height),
                                 (approach+width,3)],
                "compression_wave": [(approach,2),(approach+width*.16,-height*.18),
                                     (approach+width*.36,height*.72),(approach+width*.58,height*.18),
                                     (approach+width*.78,height),(approach+width,2.5)],
                "sky_hook": [(approach,2),(approach+width*.12,height*.45),
                             (approach+width*.3,height),(approach+width*.52,height*.86),
                             (approach+width*.7,height*.36),(approach+width,4)],
                "over_under": [(approach,2),(approach+width*.18,height*.72),
                               (approach+width*.42,height),(approach+width*.6,height*.25),
                               (approach+width*.78,-height*.2),(approach+width,2.5)],
                "blast_wall": [(approach,2),(approach+width*.1,height*.7),
                               (approach+width*.22,height),(approach+width*.4,height*.94),
                               (approach+width*.57,height*.28),(approach+width,3)]}
            if layout not in route_profiles:
                raise ValueError(f"Unknown explosive route layout: {layout}")
            points = profile(route_profiles[layout])

        joint = copy.deepcopy(points[0])
        travel = joint["tangentOut"]
        if p.get("symmetricTangents"):
            magnitude = math.hypot(travel["x"], travel["y"])
            ux, uy = travel["x"]/magnitude, travel["y"]/magnitude
            if uy <= 0:
                raise ValueError(f"{variant['id']}: entry tangent must rise from the ground")
            boundary_clearance = p.get("boundaryClearance",1)
            connector_start_x = approach-boundary_clearance
            tangent_run = points[0]["x"]-connector_start_x
            ring_handle = p.get("ringTangentHandle", tangent_run*.38)
            ground_handle = tangent_run*p.get("rampGroundHandleRatio",.42)
            start = vertex(connector_start_x, 0, outgoing=(ground_handle,0))
            joint["tangentIn"] = {"x":-ux*ring_handle,"y":-uy*ring_handle}
        else:
            connector_start_x = min(approach-5, points[0]["x"]-5)
            start = vertex(connector_start_x, 0, outgoing=(3,0))
            joint["tangentIn"] = {"x":-travel["x"],"y":-travel["y"]}
        joint["tangentOut"] = {"x":0,"y":0}
        pre_route = {"id":"pre_route", "closed":False, "points":points,
                     "metadata":{"assemblyId":"loop_assembly", "routePhase":"before_explosion",
                                 "initialState":"active", "disableAfterExplosion":True}}
        pre_line = sample_curve(pre_route, 100)
        trigger_point = pre_line[min(len(pre_line)-1, round(len(pre_line)*.42))]
        reveal_start = (points[-1]["x"], points[-1]["y"])
        reveal_length = p["revealLength"]
        reveal_end_y = p.get("exitHeight", 0)
        relief = p.get("revealRelief", 2)
        sx, sy = reveal_start
        ex = sx+reveal_length
        reveal_profiles = {
            "landing_bridge": [(sx,sy),(sx+reveal_length*.28,sy),
                               (sx+reveal_length*.7,reveal_end_y),(ex,reveal_end_y)],
            "downhill_chute": [(sx,sy),(sx+reveal_length*.18,sy-relief*.2),
                               (sx+reveal_length*.55,reveal_end_y+relief),(ex,reveal_end_y)],
            "wave_bridge": [(sx,sy),(sx+reveal_length*.22,sy+relief),
                            (sx+reveal_length*.45,(sy+reveal_end_y)/2-relief),
                            (sx+reveal_length*.72,reveal_end_y+relief),(ex,reveal_end_y)],
            "stepped_descent": [(sx,sy),(sx+reveal_length*.2,sy),
                                (sx+reveal_length*.38,sy-relief),(sx+reveal_length*.58,sy-relief),
                                (sx+reveal_length*.78,reveal_end_y+relief*.35),(ex,reveal_end_y)],
            "crown_bridge": [(sx,sy),(sx+reveal_length*.3,sy+relief),
                             (sx+reveal_length*.58,reveal_end_y+relief),(ex,reveal_end_y)],
            "bowl_bridge": [(sx,sy),(sx+reveal_length*.28,sy-relief),
                            (sx+reveal_length*.58,reveal_end_y-relief),(ex,reveal_end_y)],
            "rising_bridge": [(sx,sy),(sx+reveal_length*.32,sy+relief),
                              (sx+reveal_length*.68,reveal_end_y+relief),(ex,reveal_end_y)],
            "snap_catch": [(sx,sy),(sx+reveal_length*.14,sy-relief),
                           (sx+reveal_length*.42,reveal_end_y),(ex,reveal_end_y)],
            "long_runout": [(sx,sy),(sx+reveal_length*.2,sy),
                            (sx+reveal_length*.5,(sy+reveal_end_y)/2),(ex,reveal_end_y)]}
        reveal_profile = p["revealProfile"]
        if p.get("symmetricTangents"):
            incoming = points[-1].get("tangentIn", {})
            exit_vector = (-incoming.get("x",0),-incoming.get("y",0))
            magnitude = math.hypot(*exit_vector)
            ux, uy = exit_vector[0]/magnitude, exit_vector[1]/magnitude
            if uy >= 0:
                raise ValueError(f"{variant['id']}: exit tangent must descend to the ground")
            boundary_clearance = p.get("boundaryClearance",1)
            ex = approach+2*p["radiusX"]+boundary_clearance
            tangent_run = ex-sx
            ring_handle = p.get("ringTangentHandle", tangent_run*.38)
            ground_handle = tangent_run*p.get("rampGroundHandleRatio",.42)
            revealed_points = [vertex(sx,sy,outgoing=(ux*ring_handle,uy*ring_handle)),
                               vertex(ex,reveal_end_y,incoming=(-ground_handle,0))]
        else:
            if reveal_profile not in reveal_profiles:
                raise ValueError(f"Unknown explosive reveal profile: {reveal_profile}")
            revealed_points = profile(reveal_profiles[reveal_profile])
        revealed_route = {"id":"revealed_route", "closed":False, "points":revealed_points,
                          "metadata":{"assemblyId":"loop_assembly", "routePhase":"after_explosion",
                                      "initialState":"hidden", "collisionBeforeReveal":False,
                                      "collisionAfterReveal":True, "revealMode":"deterministic_state_swap"}}
        result["InteractableObject"].append({
            "id":"connector", "type":"explosive_ramp", "closed":False, "points":[start,joint],
            "properties":{"assemblyId":"loop_assembly", "state":"armed",
                "lifecycle":["armed","committed","detonated","route_revealed","spent"],
                "commitTrigger":{"mode":"rear_wheel_clears_route_progress",
                    "routeId":"pre_route", "normalizedProgress":.42,
                    "position":{"x":trigger_point[0],"y":trigger_point[1]}},
                "destroyAfter":"rear_wheel_clears_commit_trigger", "revealsRoute":"revealed_route",
                "postDestroyRoute":"revealed_route", "routeRevealDelaySeconds":0,
                "debrisCollision":"visual_only", "resetOnCheckpoint":True,
                "experienceSequence":["ride_pre_route","explode_behind_player",
                                      "reveal_continuation","continue_forward"],
                "anticipationCue":"armed connector sparks before commitment",
                "cameraCue":"hold the exit and hidden continuation in frame during detonation"}})
        result["RampPlatform"].extend([pre_route, revealed_route])
        final_start = (ex, reveal_end_y)
        end = (ex+p["recovery"], reveal_end_y)
        add_surface("exit_recovery", [final_start,end])
        add_surface("entry_run", [(0,0),(connector_start_x,0)])
        kill_y = min(0, reveal_end_y)-1.5
        result["deadzone"].append(linear_polygon("assembly_kill",
            [(connector_start_x-.1,kill_y),(ex+.1,kill_y),(ex+.1,kill_y-1.5),
             (connector_start_x-.1,kill_y-1.5)]))
        joins.append({"from":"connector","to":"pre_route","continuity":"C1","fromPoint":1,"toPoint":0})
        reward = pre_line+sample_curve(revealed_route, 100)
        result["rewardLine"] = [(x,y+1) for x,y in reward]
        for fraction in (.2,.5,.8):
            point_index = min(len(reward)-1, round(fraction*(len(reward)-1)))
            ax, ay = reward[point_index]
            anchors.append({'x':ax,'y':ay+1,'priority':7,'role':'explosive_state_transition'})
        physics["requiredBikeSkills"] += ["air_control", "commitment_timing"]
        physics["stateTransition"] = {"trigger":"rear_wheel", "deterministic":True,
            "before":"pre_route active; continuation hidden",
            "after":"connector disabled; revealed_route collider active",
            "failurePolicy":"visual debris never blocks the continuation"}
        if layout in ("round_loop", "spiral_exit", "broken_halo"):
            rx, ry = p["radiusX"], p["radiusY"]
            physics["loopSpeedEstimate"] = {"status":"idealized_no_losses",
                "formula":"sqrt(2*g*deltaY + g*radiusOfCurvatureAtTop)",
                "value":round(math.sqrt(2*9.81*(2*ry)+9.81*rx*rx/ry),2)}
        else:
            highest = max(y for x,y in pre_line)
            physics["entrySpeedEstimate"] = {"status":"energy_height_floor_only",
                "minimum":round(math.sqrt(max(0,2*9.81*highest)),2), "units":"units/s"}
        physics["exitDirection"] = "detonation reveals a forward continuation before the bike reaches it"
        sequence_count = int(p.get("sequenceRings", 1))
        if sequence_count > 1:
            rx, ry = p["radiusX"], p["radiusY"]
            boundary_clearance = p.get("boundaryClearance",1)
            unit_stride = 2*rx+2*boundary_clearance
            reward_routes = [pre_route,revealed_route]
            # Variant 2/3 are literal copies of variant 1 placed end-to-end.
            # Each copy owns an armed entry connector, one active ring and its
            # own hidden exit ramp; no ring reveals or destroys another ring.
            for stage in range(2, sequence_count+1):
                x_offset = (stage-1)*unit_stride
                ring_points = ellipse_arc(rx, ry, p["entryAngle"], p["exitAngle"], 8,
                                          x_offset)
                ring_id = f"pre_route_{stage}"
                ring = {"id":ring_id, "closed":False, "points":ring_points,
                        "metadata":{"assemblyId":f"loop_assembly_{stage}",
                                    "routePhase":"before_explosion", "sequenceStage":stage,
                                    "initialState":"active", "disableAfterExplosion":True}}
                entry = ring_points[0].get("tangentOut", {})
                entry_length = math.hypot(entry.get("x",0),entry.get("y",0))
                ux, uy = entry["x"]/entry_length,entry["y"]/entry_length
                connector_start = approach+x_offset-boundary_clearance
                tangent_run = ring_points[0]["x"]-connector_start
                ring_handle = p.get("ringTangentHandle",tangent_run*.38)
                ground_handle = tangent_run*p.get("rampGroundHandleRatio",.42)
                bridge_start = vertex(connector_start,0,outgoing=(ground_handle,0))
                bridge_end = copy.deepcopy(ring_points[0])
                bridge_end["tangentIn"] = {"x":-ux*ring_handle,"y":-uy*ring_handle}
                bridge_end["tangentOut"] = {"x":0,"y":0}
                ring_line = sample_curve(ring,100)
                trigger = ring_line[min(len(ring_line)-1,round(len(ring_line)*.42))]
                reveal_id = f"revealed_route_{stage}"
                exit_point = ring_points[-1]
                incoming = exit_point.get("tangentIn",{})
                exit_vector = (-incoming.get("x",0),-incoming.get("y",0))
                exit_length = math.hypot(*exit_vector)
                exit_ux,exit_uy = exit_vector[0]/exit_length,exit_vector[1]/exit_length
                reveal_end_x = approach+x_offset+2*rx+boundary_clearance
                reveal_run = reveal_end_x-exit_point["x"]
                reveal_points = [vertex(exit_point["x"],exit_point["y"],
                                         outgoing=(exit_ux*ring_handle,exit_uy*ring_handle)),
                                 vertex(reveal_end_x,0,incoming=(-reveal_run*p.get("rampGroundHandleRatio",.42),0))]
                stage_reveal = {"id":reveal_id,"closed":False,"points":reveal_points,
                    "metadata":{"assemblyId":f"loop_assembly_{stage}",
                                "routePhase":"after_explosion","sequenceStage":stage,
                                "initialState":"hidden","collisionBeforeReveal":False,
                                "collisionAfterReveal":True,"revealMode":"deterministic_state_swap"}}
                connector = {"id":f"connector_{stage}", "type":"explosive_ramp",
                    "closed":False, "points":[bridge_start,bridge_end],
                    "properties":{"assemblyId":f"loop_assembly_{stage}","state":"armed",
                        "sequenceStage":stage,
                        "lifecycle":["armed","committed","detonated","route_revealed","spent"],
                        "commitTrigger":{"mode":"rear_wheel_clears_route_progress",
                            "routeId":ring_id,"normalizedProgress":.42,
                            "position":{"x":trigger[0],"y":trigger[1]}},
                        "destroyAfter":"rear_wheel_clears_commit_trigger",
                        "revealsRoute":reveal_id,"postDestroyRoute":reveal_id,
                        "routeRevealDelaySeconds":0,"debrisCollision":"visual_only",
                        "resetOnCheckpoint":True,
                        "experienceSequence":["ride_pre_route","explode_behind_player",
                                              "reveal_continuation","continue_forward"],
                        "anticipationCue":"armed connector sparks before commitment",
                        "cameraCue":"hold the exit and hidden continuation in frame during detonation"}}
                result["InteractableObject"].append(connector)
                result["RampPlatform"].extend([ring,stage_reveal])
                joins.append({"from":connector["id"],"to":ring_id,"continuity":"C1",
                              "fromPoint":1,"toPoint":0})
                reward_routes.extend([ring,stage_reveal])
                ex = reveal_end_x

            new_exit_surface = profile([(ex,0),(ex+p["recovery"],0)])
            exit_index = next(i for i,shape in enumerate(result["MainPlatform"])
                              if shape["id"] == "exit_recovery")
            result["MainPlatform"][exit_index] = ground("exit_recovery",new_exit_surface)
            surfaces[exit_index] = new_exit_surface
            end = (ex+p["recovery"],0)
            kill_y = -1.5
            result["deadzone"] = [linear_polygon("assembly_kill",
                [(connector_start_x-.1,kill_y),(ex+.1,kill_y),(ex+.1,kill_y-1.5),
                 (connector_start_x-.1,kill_y-1.5)])]
            reward = [point for route in reward_routes
                      for point in sample_curve(route,100)]
            result["rewardLine"] = [(x,y+1) for x,y in reward]
            physics["stateTransition"].update(sequenceStages=sequence_count,
                sequence="independent copies of the round explosive loop placed end-to-end")
    else:
        raise ValueError(f"Unknown builder: {kind}")

    sampled=[]
    for surface in surfaces:
        sampled.extend(sample_curve({"points":surface,"closed":False}))
    result.update({"id":variant["id"],"type":type_id,"difficulty":variant["difficulty"],"intent":variant["intent"],
        "geometryProfile": family.get("geometryProfile", type_id),
        "ports":{"entry":{"x":0,"y":0,"direction":[1,0]},"exit":{"x":end[0],"y":end[1],"direction":[1,0]}},
        "physics":physics,"jumpUnit":units,"coinCandidates":anchors,"checkpointCandidates":checkpoints,"joins":joins,
        "camera":{"visibleLandingRequired":True,"lookAheadDistance":max(16,p.get("gap",0)+p.get("landing",0)),"verticalFeatureVisibilityRequired":kind in ("loop", "spring_jump")},
        "groundSamples":sampled,"validationStatus":"geometry_only; Unity bike playtest required"})
    assign_module_coins(result)
    return result


def place(module, x, y, instance_id):
    """Instantiate local coordinates without changing the library source."""
    placed = copy.deepcopy(module)
    for group in GROUPS:
        for item in placed[group]:
            item["id"] = f"{instance_id}_{item['id']}"
            for point in item.get("points",[]):
                point["x"] += x
                point["y"] += y
            if "transform" in item:
                item['transform']['x'] += x
                item['transform']['y'] += y
            for point in item.get('properties', {}).get('previewTrajectory', {}).get('points', []):
                point['x'] += x
                point['y'] += y
            if item.get('type') == 'SpringObject':
                target = item['properties']['targetPosition']
                target['x'] += x
                target['y'] += y
            for field in ("properties","metadata"):
                meta=item.get(field,{})
                if "assemblyId" in meta:
                    meta["assemblyId"] = f"{instance_id}_{meta['assemblyId']}"
                for reference in ("postDestroyRoute", "revealsRoute", "supportingRingId"):
                    if reference in meta:
                        meta[reference] = f"{instance_id}_{meta[reference]}"
                for references in ("revealsObjects", "destroysRoutes"):
                    if references in meta:
                        meta[references] = [f"{instance_id}_{value}" for value in meta[references]]
                if isinstance(meta.get("commitTrigger"), dict):
                    trigger = meta["commitTrigger"]
                    if "routeId" in trigger:
                        trigger["routeId"] = f"{instance_id}_{trigger['routeId']}"
                    if isinstance(trigger.get("position"), dict):
                        trigger["position"]["x"] += x
                        trigger["position"]["y"] += y
            item.setdefault("metadata",{})["sourceVariant"] = module["id"]
    for key in ("coinCandidates","checkpointCandidates"):
        for point in placed[key]:
            point["x"] += x
            point["y"] += y
    for port in placed["ports"].values():
        port["x"] += x
        port["y"] += y
    placed["groundSamples"] = [(a+x,b+y) for a,b in placed["groundSamples"]]
    if placed["jumpUnit"]:
        unit=placed["jumpUnit"]
        for interval in ("approach","launch","flight","landing","recovery"):
            unit[interval]=[value+x for value in unit[interval]]
        unit["checkpointCandidateAfterStabilization"] += x
    for joint in placed["joins"]:
        joint["from"] = f"{instance_id}_{joint['from']}"
        joint["to"] = f"{instance_id}_{joint['to']}"
    return placed


def as_level(modules, map_id, name, *, preview=False):
    level={g:[] for g in GROUPS}
    ground_samples=[]
    checkpoints=[]
    for module in modules:
        for group in GROUPS:
            level[group].extend(copy.deepcopy(module[group]))
        ground_samples.extend(module["groundSamples"])
        checkpoints.extend(module["checkpointCandidates"])
    first,last = modules[0]["ports"]["entry"],modules[-1]["ports"]["exit"]
    end_x=last["x"]-1
    all_points=[]
    for group in ("MainPlatform","RampPlatform"):
        for shape in level[group]:
            all_points.extend(sample_curve(shape))
    for shape in level["InteractableObject"]:
        if 'points' in shape:
            all_points.extend(sample_curve(shape))
        elif shape.get('type') == 'SpringObject':
            all_points.extend(spring_trajectory(shape))
        elif shape.get('type') == 'coin':
            all_points.append((shape['transform']['x'],shape['transform']['y']))
    top=max(all_points,key=lambda a:a[1])
    bottom_y=median(y for x,y in ground_samples)
    bottom=min(ground_samples,key=lambda a:abs(a[1]-bottom_y))
    coin_count = sum(o['type']=='coin' for o in level['InteractableObject'])
    if coin_count != 3*len(modules):
        raise ValueError('Each library obstacle must contribute exactly three coins')
    minimum_finish=max(8,(end_x-first["x"])*.08)
    level["CheckPoint"]=[{"id":f"cp_{i}","transform":{"x":cp["x"],"y":cp["y"],"rotation":0},"metadata":cp} for i,cp in enumerate(checkpoints) if end_x-cp["x"]>=minimum_finish]
    level.update({"version":"2.0","map":{"id":map_id,"name":name,"units":"unity","previewOnly":preview},
        "Start":{"x":first["x"]+1,"y":first["y"]+1},"End":{"x":end_x,"y":last["y"]+1},
        "Top":{"x":top[0],"y":top[1]},"Bottom":{"x":bottom[0],"y":bottom[1]},
        "design":{"variants":[m["id"] for m in modules],"validationStatus":"unvalidated_vehicle_physics","coinCount":coin_count,
                  "coinPolicy":{"perObstacle":3,"placement":"library_local"},
                  "obstacles":[{k:m[k] for k in ("id","type","geometryProfile","ports","difficulty","physics","camera","jumpUnit","joins")} for m in modules]}})
    return normalize_main_platform(level)


def build_campaign_level(number):
    """Select distinct authored variants, translate matching horizontal ports."""
    import random
    rng=random.Random(number)
    families=load_catalog()
    world=(number-1)//10+1
    pools={
        1:["hill","valley","wave","tabletop","gap","kicker","fall","rise"],
        2:["wave","tabletop","step_up","step_down","drop","platform","balance"],
        3:["gap","curved_ramp","platform","balance","explosive_loop","valley"],
        4:["gap","step_up","step_down","explosive_loop","balance","curved_ramp"],
        5:["balance","explosive_loop","gap","step_up","drop","wave"]}
    selected_types=rng.sample(pools[world],4)
    # A cinematic highlight on 10/50 tracks, not a replacement for every jump.
    if number % 5 == 0:
        selected_types[1] = 'spring_high' if (number//5) % 2 else 'spring_far'
    modules=[]
    x,y=0,0

    def append(type_id, named=None):
        nonlocal x,y
        family=families[type_id]
        variants=[v for v in family["variants"] if v["difficulty"]<=world]
        preferred=[v for v in variants if v["difficulty"]>=max(1,world-1)]
        variants=preferred or variants
        if named:
            variant=next(v for v in family["variants"] if v["id"]==type_id+"."+named)
        else:
            variant=rng.choice(variants)
        local=build_variant(family,variant)
        module=place(local,x,y,f"m{len(modules):02d}")
        modules.append(module)
        x,y=module["ports"]["exit"]["x"],module["ports"]["exit"]["y"]

    append("flat","launch_run")
    for type_id in selected_types:
        append(type_id)
        # Dedicated stable pad after each challenge. Jump modules already
        # carry a recovery interval; this pad also gives a safe restart runway.
        append("flat","checkpoint_pad")
    append("flat","finish_release")
    level=as_level(modules,f"campaign_{number:02d}",f"World {world} - Track {(number-1)%10+1:02d}")
    level["map"].update({"seed":number,"generatedFrom":"library/obstacle_catalog.json","catalogVersion":catalog_version()})
    level["design"].update({"world":world,"difficulty":max(m["difficulty"] for m in modules),"sequence":selected_types,
        "compositionStatus":"port_geometry_checked; speed compatibility and vehicle reachability pending"})
    return level


def main():
    parser=argparse.ArgumentParser(description="Inspect/export reusable obstacle variants")
    parser.add_argument("--type",help="Filter one obstacle type")
    parser.add_argument("--output",type=Path,help="Export individual preview maps to this directory")
    parser.add_argument("--atlas",type=Path,help="Render one contact sheet per type into this directory")
    args=parser.parse_args()
    families=load_catalog()
    if args.type:
        if args.type not in families:
            parser.error("Unknown type: "+args.type)
        families={args.type:families[args.type]}
    for family in families.values():
        modules=[build_variant(family,v) for v in family["variants"]]
        print(f"{family['type']}: {len(modules)} variants")
        if args.output:
            args.output.mkdir(parents=True,exist_ok=True)
            for module in modules:
                level=as_level([module],module["id"],module["id"],preview=True)
                (args.output/(module["id"]+".json")).write_text(json.dumps(level,indent=2),encoding="utf-8")
        if args.atlas:
            from matplotlib.figure import Figure
            from matplotlib.patches import Polygon
            args.atlas.mkdir(parents=True,exist_ok=True)
            fig=Figure(figsize=(15,12),layout="constrained")
            axes=fig.subplots(5,1)
            for ax,module in zip(axes,modules):
                # Contact sheets show the same finalized ground cross-section
                # as the JSON preview, not independent local underside heights.
                module = normalize_main_platform(module)
                for group,color in (("deadzone","#ef6666"),("MainPlatform","#3eae78"),("RampPlatform","#e6a12c"),("InteractableObject","#ee4444")):
                    for shape in module[group]:
                        if 'points' not in shape:
                            if shape.get('type') == 'SpringObject':
                                ax.plot(*zip(*spring_trajectory(shape)), color='#db2777', linestyle=':')
                                target = shape['properties']['targetPosition']
                                ax.scatter(target['x'], target['y'], color='#db2777', marker='x')
                            p = shape['transform']
                            ax.scatter(p['x'], p['y'], color='#db2777' if shape.get('type') == 'SpringObject' else color,
                                       marker='^' if shape.get('type') == 'SpringObject' else 'o')
                            continue
                        line=sample_curve(shape)
                        if shape.get("closed"):
                            ax.add_patch(Polygon(line,facecolor=color,edgecolor=color,alpha=.55))
                        else:
                            ax.plot(*zip(*line),color=color,linewidth=2.5)
                ax.autoscale_view()
                ax.set_aspect("equal",adjustable="datalim")
                ax.set_title(f"{module['id']} | D{module['difficulty']} | {module['intent']}",fontsize=10,loc="left")
                ax.grid(alpha=.15)
            fig.suptitle(family["type"]+" / five authored variants / geometry preview only")
            fig.savefig(args.atlas/(family["type"]+".png"),dpi=120)


if __name__=="__main__":
    main()
