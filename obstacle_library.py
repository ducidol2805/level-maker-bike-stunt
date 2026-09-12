"""Data-driven obstacle variants, local Bezier geometry, and preview export.

All physical numbers are design hypotheses, not results from Unity playtests.
The source of each variant is library/types/*.json, not a hard-coded map recipe.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from terrain_export import normalize_main_platform

ROOT = Path(__file__).resolve().parent
GROUPS = ("MainPlatform", "RampPlatform", "deadzone", "InteractableObject")


def load_catalog():
    catalog = json.loads((ROOT / "library/obstacle_catalog.json").read_text(encoding="utf-8"))
    families = {}
    for group in catalog["groups"]:
        family = json.loads((ROOT / "library" / group["path"]).read_text(encoding="utf-8"))
        families[family["type"]] = family
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
        # Both ends connect to ground; elevated body is an open ramp.
        shape_profiles = {"flat":[(0,h),(length,h)],
            "arch":[(0,h),(length/2,h+2),(length,h)],
            "rise":[(0,h),(length*.7,h+2),(length,h+2)],
            "fall":[(0,h+2),(length*.3,h+2),(length,h)],
            "wave":[(0,h),(length*.25,h+1.4),(length*.5,h),(length*.75,h+1.4),(length,h)]}
        middle = [(x+8,y) for x,y in shape_profiles[mode]]
        coords = [(0,0),(4,0)]+middle+[(length+16,0),(length+20,0)]
        add_surface("safe_fallback", [(0,0),(length+20,0)])
        ramp = {"id":"open_bridge","closed":False,"points":profile(coords)}
        result["RampPlatform"].append(ramp)
        end = (length+20,0)
        anchors.append({"x":middle[len(middle)//2][0],"y":max(y for x,y in middle)+1.2,"priority":4,"role":"alternate_high_line"})
    elif kind == "jump":
        a, length, h, angle = p["approach"],p["launch"],p["height"],math.radians(p["angle"])
        takeoff_x = a+length
        landing_x, landing_y = takeoff_x+p["gap"],h+p["delta"]
        coords = [(0,0),(a,0)] + ([(takeoff_x,h)] if length else [])
        add_surface("launch",coords,{len(coords)-1:math.tan(angle)})
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
        add_surface("landing_recovery",[(landing_x,landing_y),(catch_end_x,landing_end_y),end],{0:incoming_slope})
        # A compact strip below the lip and landing catches failed crossings.
        kill_y = min(h,landing_y)-1.5
        result["deadzone"].append(linear_polygon("gap_kill",[(takeoff_x-.1,kill_y),(landing_x+.1,kill_y),(landing_x+.1,kill_y-1.5),(takeoff_x-.1,kill_y-1.5)]))
        for fraction in (.25,.5,.75):
            dx = p["gap"]*fraction
            cy = h+dx*math.tan(angle)-9.81*dx*dx/(2*(ideal*math.cos(angle))**2)
            anchors.append({"x":takeoff_x+dx,"y":cy+1,"priority":6,"role":"estimated_flight"})
        physics["entrySpeedWindow"] = {"minimum":round(ideal*.9,2),"ideal":round(ideal,2),"maximum":round(ideal*1.1,2),"units":"units/s","status":"target_not_verified"}
        units = {"approach":[0,a],"launch":[a,takeoff_x],"flight":[takeoff_x,landing_x],"landing":[landing_x,catch_end_x],"recovery":[catch_end_x,end[0]],"launchAngle":p["angle"],"landingCategory":"precision" if p["landing"]<=6 else "flow","checkpointCandidateAfterStabilization":catch_end_x+3}
        if variant["difficulty"] >= 4:
            checkpoints.append({"x":catch_end_x+3,"y":landing_end_y+1,"expectedRestartSpeed":0,"requiresRestartTest":True})
    elif kind == "loop":
        rx,ry = p["radiusX"],p["radiusY"]
        cx,cy = p["approach"]+rx,ry+2
        angles = [math.radians(p["entryAngle"]+(p["exitAngle"]-p["entryAngle"])*i/6) for i in range(7)]
        points = []
        for i,t in enumerate(angles):
            factor = 4/3*math.tan((angles[1]-angles[0])/4)
            tangent = (-rx*math.sin(t)*factor,ry*math.cos(t)*factor)
            points.append(vertex(cx+rx*math.cos(t),cy+ry*math.sin(t),(-tangent[0],-tangent[1]) if i else (0,0),tangent if i<6 else (0,0)))
        start = vertex(p["approach"]-5,0,outgoing=(3,0))
        joint = copy.deepcopy(points[0])
        travel = joint["tangentOut"]
        joint["tangentIn"] = {"x":-travel["x"],"y":-travel["y"]}
        joint["tangentOut"] = {"x":0,"y":0}
        result["InteractableObject"].append({"id":"connector","type":"explosive_ramp","closed":False,"points":[start,joint],"properties":{"assemblyId":"loop_assembly","destroyAfter":"rear_wheel_clears_exit_join","postDestroyRoute":"fallback","resetOnCheckpoint":True}})
        result["RampPlatform"].append({"id":"loop_body","closed":False,"points":points,"metadata":{"assemblyId":"loop_assembly"}})
        end = (cx+rx+p["recovery"],0)
        add_surface("fallback",[(0,0),end])
        joins.append({"from":"connector","to":"loop_body","continuity":"C1","fromPoint":1,"toPoint":0})
        anchors.append({"x":cx,"y":cy+ry-.8,"priority":7,"role":"loop_inside_apex"})
        physics["requiredBikeSkills"] += ["air_control"]
        physics["loopSpeedEstimate"] = {"status":"idealized_no_losses", "formula":"sqrt(2*g*deltaY + g*radiusOfCurvatureAtTop)","value":round(math.sqrt(2*9.81*(cy+ry)+9.81*rx*rx/ry),2)}
        physics["exitDirection"] = "loop exit travels toward fallback; test reorientation before next obstacle"
    else:
        raise ValueError(f"Unknown builder: {kind}")

    sampled=[]
    for surface in surfaces:
        sampled.extend(sample_curve({"points":surface,"closed":False}))
    result.update({"id":variant["id"],"type":type_id,"difficulty":variant["difficulty"],"intent":variant["intent"],
        "ports":{"entry":{"x":0,"y":0,"direction":[1,0]},"exit":{"x":end[0],"y":end[1],"direction":[1,0]}},
        "physics":physics,"jumpUnit":units,"coinCandidates":anchors,"checkpointCandidates":checkpoints,"joins":joins,
        "camera":{"visibleLandingRequired":True,"lookAheadDistance":max(16,p.get("gap",0)+p.get("landing",0)),"verticalFeatureVisibilityRequired":kind=="loop"},
        "groundSamples":sampled,"validationStatus":"geometry_only; Unity bike playtest required"})
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
            for field in ("properties","metadata"):
                meta=item.get(field,{})
                if "assemblyId" in meta:
                    meta["assemblyId"] = f"{instance_id}_{meta['assemblyId']}"
                if "postDestroyRoute" in meta:
                    meta["postDestroyRoute"] = f"{instance_id}_{meta['postDestroyRoute']}"
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
    candidates=[]
    checkpoints=[]
    for module in modules:
        for group in GROUPS:
            level[group].extend(copy.deepcopy(module[group]))
        ground_samples.extend(module["groundSamples"])
        candidates.extend(module["coinCandidates"])
        checkpoints.extend(module["checkpointCandidates"])
    first,last = modules[0]["ports"]["entry"],modules[-1]["ports"]["exit"]
    end_x=last["x"]-1
    all_points=[]
    for group in ("MainPlatform","RampPlatform"):
        for shape in level[group]:
            all_points.extend(sample_curve(shape))
    for shape in level["InteractableObject"]:
        all_points.extend(sample_curve(shape))
    top=max(all_points,key=lambda a:a[1])
    bottom_y=median(y for x,y in ground_samples)
    bottom=min(ground_samples,key=lambda a:abs(a[1]-bottom_y))
    coin_count=0 if preview else 10
    ordered=sorted(candidates,key=lambda a:(-a["priority"],a["x"]))
    # Safe filler is sampled from the actual driving surface, never arbitrary y.
    ordered.extend({"x":x,"y":y+1.2,"priority":0,"role":"safe_ground"} for x,y in ground_samples[::12])
    selected=[]
    for coin in ordered:
        if len(selected)==coin_count:
            break
        if all(math.dist((coin["x"],coin["y"]),(c["x"],c["y"]))>1.2 for c in selected):
            selected.append(coin)
    for i,coin in enumerate(selected):
        level["InteractableObject"].append({"id":f"coin_{i+1:02d}","type":"coin","transform":{"x":coin["x"],"y":coin["y"]},"properties":{"optional":True,"line":coin["role"],"collectionRisk":"pending_playtest"}})
    minimum_finish=max(8,(end_x-first["x"])*.08)
    level["CheckPoint"]=[{"id":f"cp_{i}","transform":{"x":cp["x"],"y":cp["y"],"rotation":0},"metadata":cp} for i,cp in enumerate(checkpoints) if end_x-cp["x"]>=minimum_finish]
    level.update({"version":"2.0","map":{"id":map_id,"name":name,"units":"unity","previewOnly":preview},
        "Start":{"x":first["x"]+1,"y":first["y"]+1},"End":{"x":end_x,"y":last["y"]+1},
        "Top":{"x":top[0],"y":top[1]},"Bottom":{"x":bottom[0],"y":bottom[1]},
        "design":{"variants":[m["id"] for m in modules],"validationStatus":"unvalidated_vehicle_physics","coinCount":len(selected),
                  "obstacles":[{k:m[k] for k in ("id","ports","difficulty","physics","camera","jumpUnit","joins")} for m in modules]}})
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
    level["map"].update({"seed":number,"generatedFrom":"library/obstacle_catalog.json","catalogVersion":"2.0"})
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
                for group,color in (("deadzone","#ef6666"),("MainPlatform","#3eae78"),("RampPlatform","#e6a12c"),("InteractableObject","#ee4444")):
                    for shape in module[group]:
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
