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
from bike_stunt.terrain_export import normalize_main_platform, surface_count
from bike_stunt.spring_object import spring_trajectory
from bike_stunt.world_scale import scale_world_data
from bike_stunt.library_ids import validate_library_ids

ROOT = Path(__file__).resolve().parent.parent
GROUPS = ("MainPlatform", "RampPlatform", "FreePlatform", "deadzone", "InteractableObject")
DEADZONE_Y_OFFSET = -2.0
GROUND_DEPTH = 7.5  # 15 editor/map units at the catalog's 2x world scale.


def catalog_version():
    return json.loads((ROOT / "library/obstacle_catalog.json").read_text(encoding="utf-8"))["version"]


def load_catalog():
    catalog = json.loads((ROOT / "library/obstacle_catalog.json").read_text(encoding="utf-8"))
    families = {}
    actual_total = 0
    for group in catalog["groups"]:
        family = json.loads((ROOT / "library" / group["path"]).read_text(encoding="utf-8"))
        family['_worldScale'] = catalog.get('worldScale', 1)
        actual_count = len(family["variants"])
        if group.get("variantCount") != actual_count:
            raise ValueError(f"{family['type']}: catalog declares {group.get('variantCount')} variants, found {actual_count}")
        families[family["type"]] = family
        actual_total += actual_count
    if catalog.get("variantCount") != actual_total:
        raise ValueError(f"Catalog declares {catalog.get('variantCount')} variants, found {actual_total}")
    validate_library_ids(v for family in families.values() for v in family['variants'])
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


def ground_floor(surface, depth=GROUND_DEPTH):
    """Keep the underside below the full Bezier control hull."""
    heights = [p['y'] for p in surface]
    for a, b in zip(surface, surface[1:]):
        for point, key in ((a, 'tangentOut'), (b, 'tangentIn')):
            if point.get('tangentMode') != 'linear':
                heights.append(point['y'] + point.get(key, {}).get('y', 0))
    return min(heights) - depth


def ground(name, surface, *, depth=GROUND_DEPTH):
    floor = ground_floor(surface, depth)
    points = copy.deepcopy(surface)
    # Closure handles must not bulge beyond the terrain footprint.
    points[0]["tangentIn"] = {"x": 0, "y": 0}
    points[-1]["tangentOut"] = {"x": 0, "y": 0}
    points[0]["tangentMode"] = points[-1]["tangentMode"] = "broken"
    points += [vertex(surface[-1]["x"], floor, mode="linear"), vertex(surface[0]["x"], floor, mode="linear")]
    return {"id": name, "closed": True, "points": points,
            "metadata": {"drivingSurfaceCount": len(surface)}}


def editable_points(shape, group):
    """Return the driving surface without its two bottom corners."""
    if group == "MainPlatform" or (group == "RampPlatform" and shape.get("closed") and
                                   "drivingSurfaceCount" in shape.get("metadata", {})):
        return shape["points"][:surface_count(shape)]
    return shape["points"]


def apply_geometry_overrides(module, overrides):
    if not isinstance(overrides, dict):
        raise ValueError("geometryOverrides must be an object")
    for group, shapes in overrides.items():
        if group not in GROUPS or not isinstance(shapes, dict):
            raise ValueError(f"Invalid geometry override group: {group}")
        available = {shape["id"]: shape for shape in module[group] if "points" in shape}
        for shape_id, override in shapes.items():
            full_polygon = isinstance(override, dict)
            points = override.get('points') if full_polygon else override
            if shape_id not in available or not isinstance(points, list):
                raise ValueError(f"Invalid geometry override shape: {group}/{shape_id}")
            shape = available[shape_id]
            generated_underside = group == "MainPlatform" or (group == "RampPlatform" and
                shape.get("closed") and "drivingSurfaceCount" in shape.get("metadata", {}))
            if full_polygon and not generated_underside:
                raise ValueError(f"{group}/{shape_id}: full polygon override requires a terrain body")
            if full_polygon and 'freeBottomCorners' in override and not isinstance(override['freeBottomCorners'], bool):
                raise ValueError(f"{group}/{shape_id}: freeBottomCorners must be boolean")
            minimum = 2 if generated_underside or not shape.get("closed") else 4 if group == 'deadzone' else 3
            if len(points) < minimum + (2 if full_polygon else 0):
                raise ValueError(f"{group}/{shape_id} needs at least {minimum} points")
            checked = []
            for point in points:
                if not isinstance(point, dict) or point.get("tangentMode") not in ("linear", "broken", "continuous"):
                    raise ValueError(f"Invalid point or tangent mode in {group}/{shape_id}")
                values = [point.get("x"), point.get("y")]
                for key in ("tangentIn", "tangentOut"):
                    tangent = point.get(key)
                    if not isinstance(tangent, dict):
                        raise ValueError(f"Missing {key} in {group}/{shape_id}")
                    values.extend((tangent.get("x"), tangent.get("y")))
                if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
                    raise ValueError(f"Non-finite point in {group}/{shape_id}")
                checked.append(copy.deepcopy(point))
            if generated_underside:
                count = override.get('drivingSurfaceCount') if full_polygon else len(checked)
                merged_corners = override.get('mergedClosePoints', shape.get('metadata', {}).get('mergedClosePoints', False)) if full_polygon else shape.get('metadata', {}).get('mergedClosePoints', False)
                corner_count = len(checked)-count if isinstance(count, int) else -1
                valid_corner_count = corner_count == 2 or (merged_corners and corner_count in (0, 1))
                if full_polygon and (not isinstance(count, int) or not valid_corner_count):
                    raise ValueError(f"{group}/{shape_id}: invalid driving surface count")
                surface = checked[:count]
                if any(a["x"] > b["x"] for a, b in zip(surface, surface[1:])):
                    raise ValueError(f"{shape_id}: driving surface must run left to right")
                original = editable_points(shape, group)
                if group == "MainPlatform":
                    for port_name, before, after in (("entry", original[0], surface[0]),
                                                     ("exit", original[-1], surface[-1])):
                        port = module["ports"][port_name]
                        if abs(port["x"]-before["x"]) < 1e-6 and abs(port["y"]-before["y"]) < 1e-6:
                            port.update(x=after["x"], y=after["y"])
                if full_polygon:
                    candidate = {**shape, 'points': checked,
                                 'metadata': {**shape.get('metadata', {}), 'drivingSurfaceCount': count,
                                              'freeBottomCorners': override.get('freeBottomCorners', False),
                                              'mergedClosePoints': merged_corners}}
                    surface_count(candidate)
                    shape['points'] = checked
                    if candidate['metadata']['freeBottomCorners']:
                        shape.setdefault('metadata', {})['freeBottomCorners'] = True
                    if candidate['metadata']['mergedClosePoints']:
                        shape.setdefault('metadata', {})['mergedClosePoints'] = True
                else:
                    underside = copy.deepcopy(shape["points"][-2:])
                    ceiling = ground_floor(surface) if group == 'MainPlatform' else min(p['y'] for p in surface)-.01
                    floor = min(underside[0]["y"], underside[1]["y"], ceiling)
                    underside[0].update(x=surface[-1]["x"], y=floor)
                    underside[1].update(x=surface[0]["x"], y=floor)
                    shape["points"] = checked + underside
                shape.setdefault("metadata", {})["drivingSurfaceCount"] = count
            else:
                shape["points"] = checked


def linear_polygon(name, coords):
    return {"id": name, "closed": True, "points": [vertex(x,y,mode="linear") for x,y in coords]}


def platform_body(name, surface):
    """Trim redundant ground-level tails and close just below the fallback road.

    The 0.01-unit overlap avoids duplicate vertices at ground-touching ends
    and leaves no visible gap between the platform base and the ground.
    """
    def ground_flat(a, b):
        return (a["y"] == b["y"] == 0 and
                a["tangentOut"]["y"] == b["tangentIn"]["y"] == 0)

    surface = list(surface)
    while len(surface) > 2 and ground_flat(surface[0], surface[1]):
        surface.pop(0)
    while len(surface) > 2 and ground_flat(surface[-2], surface[-1]):
        surface.pop()
    shape = ground(name, surface)
    for point in shape["points"][-2:]:
        point["y"] = -.01
    return shape


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
        routes = [([(x,y+1) for x,y in sample_curve(
                    {'points': shape['points'][:surface_count(shape)]}
                    if shape.get('metadata', {}).get('drivingSurfaceCount') else shape,120)],None)
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


def build_variant(family, variant, *, scale=1):
    from bike_stunt.library_variants import build_variant as _build_variant
    return _build_variant(family, variant, scale=scale, deadzone_y_offset=DEADZONE_Y_OFFSET)


def place(module, x, y, instance_id):
    """Instantiate local coordinates without changing the library source."""
    placed = copy.deepcopy(module)
    id_map = {item['id']: (f"{instance_id}_{module['libID']}_{item['id']}"
                          if 'points' in item else f"{instance_id}_{item['id']}")
              for group in GROUPS for item in module[group]}
    for group in GROUPS:
        for item in placed[group]:
            local_id = item['id']
            item['id'] = id_map[local_id]
            if 'points' in item:
                item.setdefault('metadata', {}).update(libID=module['libID'], libName=local_id)
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
                for reference in ("postDestroyRoute", "revealsRoute", "supportingRingId", "supportingShapeId"):
                    if reference in meta:
                        meta[reference] = id_map[meta[reference]]
                for references in ("revealsObjects", "destroysRoutes"):
                    if references in meta:
                        meta[references] = [id_map[value] for value in meta[references]]
                if isinstance(meta.get("commitTrigger"), dict):
                    trigger = meta["commitTrigger"]
                    if "routeId" in trigger:
                        trigger["routeId"] = id_map[trigger['routeId']]
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
        joint["from"] = id_map[joint['from']]
        joint["to"] = id_map[joint['to']]
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
    for group in ("MainPlatform","RampPlatform","FreePlatform"):
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
                  "obstacles":[{k:m[k] for k in ("id","libID","type","geometryProfile","ports","difficulty","physics","camera","jumpUnit","joins")} for m in modules]}})
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
        local=build_variant(family,variant,scale=1)
        module=place(local,x,y,f"s{len(modules):02d}")
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
        modules=[build_variant(family,v,scale=family['_worldScale']) for v in family["variants"]]
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
                # Validate the same authored cross-section used by JSON previews.
                module = normalize_main_platform(module)
                for group,color in (("deadzone","#ef6666"),("MainPlatform","#3eae78"),("RampPlatform","#e6a12c"),("FreePlatform","#a78bfa"),("InteractableObject","#ee4444")):
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
