"""Obstacle variant construction rules."""
from bike_stunt.obstacle_library import (ROOT, GROUPS, DEADZONE_Y_OFFSET, vertex, profile, sample_curve, ground, editable_points, apply_geometry_overrides, linear_polygon, platform_body, assign_module_coins)
from bike_stunt.terrain_export import normalize_main_platform, surface_count
from bike_stunt.spring_object import spring_trajectory
from bike_stunt.spring_fallback import build_tunnel, merge_approach
from bike_stunt.world_scale import scale_world_data
import copy, json, math
from statistics import median

PLATFORM_POINT_MERGE_DISTANCE = 0.05
PLATFORM_MERGED_COORDINATE_DIGITS = 2

def merge_close_platform_points(shape, tolerance=PLATFORM_POINT_MERGE_DISTANCE):
    """Collapse adjacent platform vertices closer than tolerance in source units."""
    points = shape.get("points", [])
    metadata = shape.setdefault("metadata", {})
    surface_count = metadata.get("drivingSurfaceCount", len(points))
    closed = shape.get("closed", False)
    changed = False

    def merge_pair(keep_index, remove_index):
        nonlocal surface_count, changed
        keep, remove = points[keep_index], points[remove_index]
        keep["x"] = round((keep["x"] + remove["x"]) / 2, PLATFORM_MERGED_COORDINATE_DIGITS)
        keep["y"] = round((keep["y"] + remove["y"]) / 2, PLATFORM_MERGED_COORDINATE_DIGITS)
        # Keep handles and flags from the driving vertex; only its position is averaged.
        if keep_index < surface_count and remove_index < surface_count:
            surface_count -= 1
        else:
            metadata["mergedClosePoints"] = True
        del points[remove_index]
        changed = True

    index = 0
    while index < len(points) - 1:
        a, b = points[index], points[index + 1]
        if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < tolerance:
            merge_pair(index, index + 1)
        else:
            index += 1

    # Closed bodies also have a neighbor across the last-to-first seam. Keep
    # the first vertex's tangent data when collapsing that seam.
    if closed and len(points) > 1:
        first, last = points[0], points[-1]
        if math.hypot(first["x"] - last["x"], first["y"] - last["y"]) < tolerance:
            first["x"] = round((first["x"] + last["x"]) / 2, PLATFORM_MERGED_COORDINATE_DIGITS)
            first["y"] = round((first["y"] + last["y"]) / 2, PLATFORM_MERGED_COORDINATE_DIGITS)
            if len(points) - 1 < surface_count:
                surface_count -= 1
            else:
                metadata["mergedClosePoints"] = True
            points.pop()
            changed = True

    if changed and "drivingSurfaceCount" in metadata:
        metadata["drivingSurfaceCount"] = surface_count
    return changed

def merge_close_points_in_platforms(module):
    """Merge tiny seams in generated or authored platform ramp polygons."""
    for shape in module.get("RampPlatform", []):
        if shape.get("closed") and len(shape.get("points", [])) > 1:
            merge_close_platform_points(shape)

def build_variant(family, variant, *, scale=1, deadzone_y_offset=DEADZONE_Y_OFFSET):
    p = variant["parameters"]
    kind = family["builder"]
    type_id = family["type"]
    result = {key: [] for key in GROUPS}
    anchors, surfaces, checkpoints, joins = [], [], [], []
    physics = {"status": "unvalidated", "requiredBikeSkills": [family["primarySkill"]],
               "entrySpeedWindow": None, "exitSpeedWindow": None}
    units = None

    def add_surface(name, coords, slopes=None, *, smooth_launch=False):
        surface = profile(coords, slopes)
        if smooth_launch:
            # Replace the shoulder before the lip with one concave-up cubic.
            # Preserve the scoop's approach/basin and the authored lip angle.
            start, end = surface[-3], surface[-1]
            width, rise = end["x"]-start["x"], end["y"]-start["y"]
            slope = slopes[len(coords)-1]
            if not 0 < rise < slope*width:
                raise ValueError("Smooth launch needs an exit slope above its average slope")
            out_x = min(width/3, (width-rise/slope)*.5)
            in_x = min(width/3, rise/(2*slope))
            # Control polygon slopes: 0 <= middle <= exit slope. Therefore
            # curvature cannot turn downward anywhere on the ascent.
            start["tangentOut"] = {"x": out_x, "y": 0}
            end["tangentIn"] = {"x": -in_x, "y": -in_x*slope}
            surface.pop(-2)
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
                    if gap > 2:
                        raise ValueError(f"{variant['id']}: platform gaps must not exceed 2 units")
                    gaps.append([previous_end, coords[0][0]])
                previous_end = coords[-1][0]
                ramp = platform_body(f"open_bridge_{index}", profile(coords))
                ramp["metadata"].update(floatingSegment=False,
                                        requiresMomentumTransfer=index < len(broken_segments))
                result["RampPlatform"].append(ramp)
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
            # Both ends connect to ground; elevated body is a closed platform. Entry
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
            ramp = platform_body("open_bridge", profile(coords))
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
        launch_surface = add_surface("launch", coords, slopes, smooth_launch=type_id in
                                     {"curved_ramp", "gap", "step_up", "step_down"})
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
        if p.get('fallbackRoute') == 'tunnel':
            road, roof, zone, lower = build_tunnel(lip, launch_y, landing_coords, p)
            if p['platformConnection'] == 'connected':
                merged = merge_approach(result['MainPlatform'][-1], road)
                result['MainPlatform'][-1] = merged
                surfaces[-1] = merged['points'][:surface_count(merged)]
            else:
                result['MainPlatform'].append(road)
                surfaces.append(lower)
            result['FreePlatform'].append(roof)
            if zone is not None:
                result['deadzone'].append(zone)
            physics['missedSpring'] = {
                'route': ('ride connected lower road beneath floating catch, climb to exit'
                          if p['platformConnection'] == 'connected' else
                          'clear the short deadzone gap to reach the lower tunnel road'),
                'penalty': 'lost momentum on the drop and a longer unassisted uphill ride',
                'status': 'geometry_checked; traversal time and bike clearance need Unity playtest'}
        else:
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
        if p.get('fallbackRoute') != 'tunnel':
            result['deadzone'].append(linear_polygon('spring_gap_kill',
                [(lip-.1,min(launch_y,landing_y)-1.5),(landing_x+.1,min(launch_y,landing_y)-1.5),
                 (landing_x+.1,min(launch_y,landing_y)-3),(lip-.1,min(launch_y,landing_y)-3)]))
        arc = spring_trajectory(spring)
        for fraction in (.15, .325, .5, .675, .85):
            x, y = arc[round(fraction*(len(arc)-1))]
            anchors.append({'x':x, 'y':y, 'priority':7, 'role':'cinematic_spring_flight'})
        checkpoint_x = end_x-p['recovery']*.15 if p.get('fallbackRoute') == 'tunnel' else catch_end+3
        units = {'approach':[0,lip], 'launch':[lip,lip], 'flight':[lip,landing_x],
            'landing':[landing_x,catch_end], 'recovery':[catch_end,end[0]],
            'launchAngle': math.degrees(math.atan2(height+.5*9.81*duration**2, distance)),
            'landingCategory':'safe', 'checkpointCandidateAfterStabilization':checkpoint_x,
            'launchMechanism':'SpringObject'}
        checkpoints.append({'x':checkpoint_x,'y':end[1]+clearance,
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
    result.update({"id":variant["id"],"libID":variant["libID"],"type":type_id,"difficulty":variant["difficulty"],"intent":variant["intent"],
        "geometryProfile": family.get("geometryProfile", type_id),
        "ports":{"entry":{"x":0,"y":0,"direction":[1,0]},"exit":{"x":end[0],"y":end[1],"direction":[1,0]}},
        "physics":physics,"jumpUnit":units,"coinCandidates":anchors,"checkpointCandidates":checkpoints,"joins":joins,
        "camera":{"visibleLandingRequired":True,"lookAheadDistance":max(16,p.get("gap",0)+p.get("landing",0)),"verticalFeatureVisibilityRequired":kind in ("loop", "spring_jump")},
        "groundSamples":sampled,"validationStatus":"geometry_only; Unity bike playtest required"})
    # Apply once in local space after all family-specific zones are finalized.
    # Placement and export only carry these coordinates; never reapply there.
    for zone in result["deadzone"]:
        for point in zone["points"]:
            point["y"] += deadzone_y_offset

    # Open ramps and explosive loop routes are continuous curves, including
    # their endpoints. Keep the authored handles; only normalize the mode.
    for shape in result["RampPlatform"]:
        # Closed platform bodies retain broken edge joins and linear bottoms.
        if shape.get("closed"):
            continue
        for point in shape.get("points", []):
            point["tangentMode"] = "continuous"
    for item in result["InteractableObject"]:
        if item.get("type") == "explosive_ramp":
            for point in item.get("points", []):
                point["tangentMode"] = "continuous"

    if variant.get("geometryOverrides"):
        apply_geometry_overrides(result, variant["geometryOverrides"])
        result["groundSamples"] = [point for shape in result["MainPlatform"]
                                   for point in sample_curve({"points": editable_points(shape, "MainPlatform")})]

    assign_module_coins(result)
    added_shapes = variant.get('addedShapes', {})
    if not isinstance(added_shapes, dict) or set(added_shapes) - {'MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone'}:
        raise ValueError(f"{variant['id']}: invalid addedShapes group")
    known_ids = {item['id'] for group in GROUPS for item in result[group] if 'id' in item}
    for group in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone'):
        shapes = added_shapes.get(group, [])
        if not isinstance(shapes, list):
            raise ValueError(f"{variant['id']}: invalid addedShapes/{group}")
        for shape in shapes:
            if not isinstance(shape, dict) or not isinstance(shape.get('id'), str) or shape['id'] in known_ids:
                raise ValueError(f"{variant['id']}: invalid or duplicate added shape ID")
            if group == 'MainPlatform' or (group == 'RampPlatform' and shape.get('closed') and
                                           'drivingSurfaceCount' in shape.get('metadata', {})):
                surface_count(shape)
            elif group == 'RampPlatform' and shape.get('closed') and len(shape.get('points', [])) < 4:
                raise ValueError(f"{variant['id']}: closed platform needs 4+ points")
            elif group == 'deadzone' and (not shape.get('closed') or len(shape.get('points', [])) < 4):
                raise ValueError(f"{variant['id']}: deadzone needs a closed polygon with 4+ points")
            elif group == 'FreePlatform' and len(shape.get('points', [])) < (3 if shape.get('closed') else 2):
                raise ValueError(f"{variant['id']}: free platform needs at least 2 open or 3 closed points")
            sample_curve(shape)
            known_ids.add(shape['id'])
            result[group].append(copy.deepcopy(shape))
    conversions = variant.get('shapeTypeOverrides', {})
    if not isinstance(conversions, dict):
        raise ValueError(f"{variant['id']}: shapeTypeOverrides must be an object")
    for shape_id, override in conversions.items():
        if not isinstance(override, dict):
            raise ValueError(f"{variant['id']}: invalid shape type override for {shape_id}")
        target = override.get('group')
        shape = override.get('shape')
        matches = [(group, existing) for group in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone')
                   for existing in result[group] if existing['id'] == shape_id]
        if target not in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone') or len(matches) != 1 or not isinstance(shape, dict) or shape.get('id') != shape_id:
            raise ValueError(f"{variant['id']}: invalid shape type override for {shape_id}")
        if target == 'MainPlatform' or (target == 'RampPlatform' and shape.get('closed') and
                                        'drivingSurfaceCount' in shape.get('metadata', {})):
            surface_count(shape)
        elif target == 'deadzone' and (not shape.get('closed') or len(shape.get('points', [])) < 4):
            raise ValueError(f"{variant['id']}: deadzone needs a closed polygon with 4+ points")
        elif target == 'FreePlatform' and len(shape.get('points', [])) < (3 if shape.get('closed') else 2):
            raise ValueError(f"{variant['id']}: free platform needs at least 2 open or 3 closed points")
        sample_curve(shape)
        old_group, old_shape = matches[0]
        result[old_group].remove(old_shape)
        result[target].append(copy.deepcopy(shape))
    if result['MainPlatform'] and (added_shapes.get('MainPlatform') or conversions):
        result['groundSamples'] = [point for shape in result['MainPlatform']
                                   for point in sample_curve({'points': editable_points(shape, 'MainPlatform')})]
    if type_id == 'platform':
        merge_close_points_in_platforms(result)
    if scale != 1:
        result = scale_world_data(result, scale)
        result['worldScale'] = scale
    return result

