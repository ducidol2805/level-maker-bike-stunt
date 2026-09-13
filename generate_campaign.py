#!/usr/bin/env python3
"""Build reproducible, authored campaign maps and verify their geometry.

Tracks 01-10 use library/campaign_recipes.json, informed by Cinder Crown.
Later tracks retain the procedural catalog workflow. Physics checks are
point-mass estimates, never a substitute for Unity bike playtests.
"""
import argparse
import bisect
import copy
import json
import math
from pathlib import Path
from statistics import median
from obstacle_library import (ROOT, GROUPS, as_level, build_campaign_level,
                              build_variant, ground, load_catalog, place,
                              profile, sample_curve)
from spring_object import spring_trajectory
from terrain_export import export_level, surface_count

GRAVITY = 9.81


def height_at(line, x):
    """Interpolate a sampled left-to-right driving line, excluding closures."""
    if x < line[0][0]-1e-8 or x > line[-1][0]+1e-8:
        return None
    i = min(len(line)-1, max(1, bisect.bisect_right(line, (x, math.inf))))
    a, b = line[i-1], line[i]
    if b[0] == a[0]:
        return max(a[1], b[1])
    return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])


def road_lines(module):
    return [sample_curve({'points': s['points'][:surface_count(s)]}, 100)
            for s in module['MainPlatform']]


def flight_check(module, speed):
    """First contact with the actual sampled catch, using a wheel-contact point."""
    unit = module['jumpUnit']
    launch = module['MainPlatform'][0]['points'][surface_count(module['MainPlatform'][0])-1]
    x0, y0 = launch['x'], launch['y']
    angle = math.radians(unit['launchAngle'])
    vx, vy = speed*math.cos(angle), speed*math.sin(angle)
    catch = road_lines(module)[1]

    def flight_y(x):
        t = (x-x0)/vx
        return y0+vy*t-.5*GRAVITY*t*t

    if flight_y(catch[0][0]) < catch[0][1]:
        return {'result': 'undershoot', 'speed': round(speed, 4)}
    previous = catch[0][0]
    x = previous + .035
    while x <= catch[-1][0]:
        if flight_y(x) <= height_at(catch, x):
            lo, hi = previous, x
            for _ in range(25):
                mid = (lo+hi)/2
                if flight_y(mid) > height_at(catch, mid):
                    lo = mid
                else:
                    hi = mid
            cx = (lo+hi)/2
            left, right = max(catch[0][0], cx-.04), min(catch[-1][0], cx+.04)
            slope = (height_at(catch, right)-height_at(catch, left))/(right-left)
            incoming = (vy-GRAVITY*(cx-x0)/vx)/vx
            mismatch = abs(math.degrees(math.atan(incoming)-math.atan(slope)))
            return {'result': 'catch', 'speed': round(speed, 4),
                    'x': cx, 'y': flight_y(cx), 'airtime': (cx-x0)/vx,
                    'pitchMismatchDegrees': round(mismatch, 2),
                    'landingQuality': 'aligned' if mismatch <= 20 else 'hard_impact'}
        previous, x = x, x+.035
    return {'result': 'overshoot', 'speed': round(speed, 4)}


def tune_jump(module, variant):
    """Fit a catch for a range of velocities, then retain full flat recovery.

    With dx measured from takeoff, catch(dx) = h + (tan(angle)-offset)*dx
    - A*dx**2. At each velocity's first contact the surface derivative differs
    from the ballistic derivative by offset. This spreads good contact over
    a face, instead of fitting just one grazing trajectory. Bezier handles
    represent the quadratic exactly, followed by a smooth flattening segment.
    """
    p, unit = variant['parameters'], module['jumpUnit']
    lip = p['approach']+p['launch']
    landing_x, landing_y = lip+p['gap'], p['height']+p['delta']
    inset = min(1.5, p['landing']*.12, p['gap']*.15)
    distance = p['gap']+inset
    angle = math.radians(p['angle'])
    slope = (2*p['delta']/distance-math.tan(angle)+.20)/(1-2*inset/distance)
    if slope >= -.05:
        raise ValueError(f"{variant['id']}: needs an authored descending catch")
    target_delta = p['delta']+slope*inset
    speed = math.sqrt(GRAVITY*distance**2 /
                      (2*math.cos(angle)**2*(distance*math.tan(angle)-target_delta)))
    if module['type'] == 'drop':
        face = min(5.5, max(2.5, p['landing']*.38))
        blend = p['landing']-face
        face_y = landing_y+slope*face
        floor_y = face_y+slope*blend*.5
        coords = [(landing_x, landing_y), (landing_x+face, face_y)]
        slopes = {0:slope,1:slope}
    else:
        incoming = 2*p['delta']/p['gap']-math.tan(angle)
        offset = min(.52, math.tan(math.atan(incoming)+math.radians(18))-incoming)
        face = p['landing']*(.65 if module['type']=='step_up' else .8)
        blend = max(2 if module['type']=='step_up' else 4, p['landing']*.25)
        a = (p['gap']*(math.tan(angle)-offset)-p['delta'])/p['gap']**2
        def catch_y(dx):
            return p['height']+dx*(math.tan(angle)-offset)-a*dx*dx
        def catch_slope(dx):
            return math.tan(angle)-offset-2*a*dx
        coords = [(landing_x+dx,catch_y(p['gap']+dx)) for dx in (0,face*.5,face)]
        slopes = {i:catch_slope(p['gap']+dx) for i,dx in enumerate((0,face*.5,face))}
        floor_y = coords[-1][1]+slopes[2]*blend*.5
    catch_end = landing_x+face+blend
    end_x = catch_end+p['recovery']
    surface = profile(coords+[(catch_end, floor_y), (end_x, floor_y)], slopes)
    module['MainPlatform'][1] = ground('landing_recovery', surface)
    module['ports']['exit'].update(x=end_x, y=floor_y)
    module['groundSamples'] = [pt for line in road_lines(module) for pt in line]
    unit.update(landing=[landing_x, catch_end], recovery=[catch_end, end_x],
                checkpointCandidateAfterStabilization=catch_end+3,
                landingCategory='flow')
    module['checkpointCandidates'] = [dict(x=catch_end+3, y=floor_y+1,
        expectedRestartSpeed=0, requiresRestartTest=True)]
    checked = [flight_check(module, speed*(.8+i*.005)) for i in range(161)]
    # Choose the center of the widest contiguous aligned window. Do not imply
    # that separated successful samples form one continuous speed window.
    runs, run = [], []
    for result in checked:
        if result.get('landingQuality') == 'aligned':
            run.append(result['speed'])
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    if not runs:
        raise ValueError(f"{variant['id']}: no aligned point-mass catch")
    aligned = max(runs, key=lambda r:r[-1]-r[0])
    speed = (aligned[0]+aligned[-1])/2
    ideal = flight_check(module, speed)
    if ideal['result'] != 'catch' or ideal['landingQuality'] != 'aligned':
        raise ValueError(f"{variant['id']}: ideal catch failed: {ideal}")
    samples = []
    for factor in (.8, .9, 1, 1.1, 1.2):
        samples.append(dict(multiplier=factor, **flight_check(module, speed*factor)))
    module['physics'].update(
        entrySpeedWindow={'minimum': min(aligned), 'ideal': speed, 'maximum': max(aligned),
                          'units': 'units/s', 'status': 'sampled_point_mass_only'},
        pointMassCheck={'coordinateSpace': 'module_local', 'ideal': ideal, 'sweep': samples,
                        'limitations': 'No bike size, suspension, wheel collision, motor or air control.'},
        catchDesign='Velocity-envelope catch, Bezier compression, full flat recovery' if module['type']!='drop'
                    else 'Sloped drop catch, Bezier compression, full flat recovery')
    # Bike reference point is one unit above the modeled contact trajectory.
    duration = ideal['airtime']
    module['rewardLine'] = [(lip+speed*math.cos(angle)*duration*i/100,
        p['height']+1+speed*math.sin(angle)*duration*i/100-
        .5*GRAVITY*(duration*i/100)**2) for i in range(101)]


def distribute(line, count, start=.12, end=.88):
    """Equal arc-length spacing on an actual route, not uniform world X."""
    distances = [0.0]
    for a, b in zip(line, line[1:]):
        distances.append(distances[-1]+math.dist(a, b))
    result = []
    for i in range(count):
        fraction = .5 if count == 1 else start+(end-start)*i/(count-1)
        target = distances[-1]*fraction
        j = min(len(line)-1, max(1, bisect.bisect_right(distances, target)))
        ratio = (target-distances[j-1]) / max(1e-12, distances[j]-distances[j-1])
        result.append(tuple(line[j-1][k]+ratio*(line[j][k]-line[j-1][k]) for k in (0, 1)))
    return result


def add_barrel(module, beat):
    ramp = sample_curve(module['RampPlatform'][0], 120)
    # Use the last crest so the reward arc cannot hit a second raised roller.
    maximum = max(y for x, y in ramp)
    x0, surface_y = max((pt for pt in ramp if abs(pt[1]-maximum)<1e-5), key=lambda p:p[0])
    x0 += .05
    y0 = height_at(ramp, x0)+1
    entry_speed = beat.get('barrelEntrySpeed', 7.0)
    impulse_y = beat.get('barrelImpulseY', 5.0)
    vx, vy = entry_speed+3, impulse_y
    lines = road_lines(module)+[ramp]

    def clearance(t):
        x, y = x0+vx*t, y0+vy*t-.5*GRAVITY*t*t
        heights = [height_at(line, x) for line in lines]
        heights = [h for h in heights if h is not None]
        return y-1-max(heights) if heights else -math.inf

    limit = (module['ports']['exit']['x']-x0-3)/vx
    t, previous = .02, 0
    while t < limit and clearance(t) > 0:
        previous, t = t, t+.01
    if t >= limit:
        raise ValueError(f"{module['id']}: barrel has no catch before its recovery")
    lo, hi = previous, t
    for _ in range(30):
        mid = (lo+hi)/2
        if clearance(mid)>0:
            lo = mid
        else:
            hi = mid
    duration = (lo+hi)/2
    arc = [(x0+vx*duration*i/80, y0+vy*duration*i/80-.5*GRAVITY*(duration*i/80)**2)
           for i in range(81)]
    module['rewardLine'] = arc
    module['InteractableObject'].append({'id': 'barrel', 'type': 'explosive_barrel',
        'transform': {'x': x0, 'y': y0-.15, 'rotation': 0},
        'properties': {'force': {'x': 3, 'y': impulse_y}, 'forceMode': 'impulse',
            'activation': 'on_player_contact', 'optionalRoute': True,
            'postDestroyRoute': 'safe_fallback', 'resetOnCheckpoint': True,
            'beatId': beat['beat'], 'purpose': beat['purpose'],
            'previewTrajectory': {'status': 'point_mass_hypothesis_not_vehicle_simulation',
                'assumedMass': 1, 'assumedEntryVelocity': {'x': entry_speed, 'y': 0},
                'gravity': GRAVITY, 'points': [{'x':x,'y':y} for x,y in arc]},
            'landingPosition': {'x': arc[-1][0], 'y': arc[-1][1]},
            'validationStatus': 'First contact with sampled bridge/ground checked; real impulse tuning pending'}})
    module['physics']['barrelLaunch'] = {'status': 'point_mass_only',
        'coordinateSpace': 'module_local', 'landing': arc[-1],
        'clearRunout': module['ports']['exit']['x']-arc[-1][0]}


def author_module(families, stage):
    type_id = stage['variant'].split('.')[0]
    family = families[type_id]
    variant = copy.deepcopy(next(v for v in family['variants'] if v['id']==stage['variant']))
    if 'landingLength' in stage:
        if family['builder'] != 'jump' or stage['landingLength']<=0:
            raise ValueError('Landing-length override requires a positive jump catch length')
        variant['parameters']['landing'] = stage['landingLength']
    if 'length' in stage:
        if family['builder'] != 'surface':
            raise ValueError('Length override only supports authored surface variants')
        factor = stage['length']/variant['parameters']['surface'][-1][0]
        for point in variant['parameters']['surface']:
            point[0] *= factor
    module = build_variant(family, variant)
    if family['builder'] == 'loop':
        module['loopParameters'] = variant['parameters']
    if family['builder'] == 'jump':
        tune_jump(module, variant)
    if family['builder'] == 'technical':
        surface = module['MainPlatform'][0]['points'][:-2]
        for i, point in enumerate(surface):
            if ((i and surface[i-1]['x']==point['x']) or
                    (i+1 < len(surface) and surface[i+1]['x']==point['x'])):
                point['corner'] = True
    if stage.get('barrel'):
        add_barrel(module, stage)
    if stage.get('boosts'):
        connector = next(o for o in module['InteractableObject'] if o['type']=='explosive_ramp')
        line = sample_curve(connector, 120)
        locations = distribute(line, stage['boosts'], .57, .83)
        # A single boost goes in the upper half, after branch commitment.
        if len(locations)==1:
            locations = distribute(line, 2, .64, .65)[:1]
        for i, (x,y) in enumerate(locations, 1):
            module['InteractableObject'].append({'id':f'boost_{i}', 'type':'speed_boost',
                'transform':{'x':x,'y':y+.85},
                'properties':{'durationSeconds':3,'stackable':True,
                    'speedMultiplier':1.18 if stage['boosts']==1 else 1.12,
                    'optionalRoute':True, 'supportingShapeId':'connector',
                    'beatId':stage['beat'], 'purpose':stage['purpose'],
                    'validationStatus':'Check zero/one/two pickups and real loop entry speed in Unity'}})
    return module


def coin_line(module, stage):
    if 'rewardLine' in module:
        return module['rewardLine'], 'estimated_flight', .06, .90
    if module['type'].startswith('spring_'):
        spring = next(o for o in module['InteractableObject'] if o['type']=='SpringObject')
        return spring_trajectory(spring, 160), 'cinematic_spring_flight', .10, .84
    if module['type']=='explosive_loop':
        p = module['loopParameters']
        cx = module['ports']['entry']['x']+p['approach']+p['radiusX']
        cy = module['ports']['entry']['y']+p['radiusY']+2
        inner = [(cx+(p['radiusX']-.85)*math.cos(math.pi*i/180),
                  cy+(p['radiusY']-.85)*math.sin(math.pi*i/180)) for i in range(181)]
        return inner, 'loop_inside_arc', .02, .98
    if module['RampPlatform']:
        line = sample_curve(module['RampPlatform'][0], 120)
        baseline = module['ports']['entry']['y']
        line = [(x,y+1) for x,y in line if y>baseline+.8]
        return line, 'alternate_high_line', .15, .85
    line = road_lines(module)[0]
    return [(x,y+1.2) for x,y in line], 'crest_or_flow', .20, .80


def build_authored_level(recipe, families=None):
    families = families or load_catalog()
    stages = [dict(variant='flat.launch_run', beat='start', role='setup', tension=1,
                   coins=0, purpose='Clear runway; show the first lesson before commitment.')]
    stages += recipe['stages']
    stages += [dict(variant='flat.finish_release', beat='finish', role='resolution', tension=0,
                   coins=0, length=recipe['finishLength'], purpose='Empty finish release after the last challenge.')]
    if sum(s['coins'] for s in stages)!=20:
        raise ValueError(f"{recipe['name']}: coin budgets must total 20")
    modules, coins, checkpoints, beats, sequence = [], [], [], [], []
    x, y = 0, 0
    for i, stage in enumerate(stages):
        local = author_module(families, stage)
        module = place(local, x, y, f's{i:02d}')
        if 'rewardLine' in local:
            module['rewardLine'] = [(px+x,py+y) for px,py in local['rewardLine']]
        for obj in module['InteractableObject']:
            props = obj.setdefault('properties', {})
            props.update(beatId=stage['beat'], purpose=stage['purpose'])
            if 'supportingShapeId' in props:
                props['supportingShapeId'] = f"s{i:02d}_"+props['supportingShapeId']
            if 'landingPosition' in props:
                props['landingPosition']['x'] += x
                props['landingPosition']['y'] += y
        modules.append(module)
        stop = module['ports']['exit']
        sequence.append({'instanceId':f's{i:02d}', 'variant':stage['variant'],
                         'beatId':stage['beat'], 'role':stage['role'], 'tension':stage['tension'],
                         'purpose':stage['purpose'], 'xRange':[x,stop['x']],
                         'entryY':y, 'exitY':stop['y'],
                         'parameterOverrides':{k:stage[k] for k in ('length','landingLength','barrelEntrySpeed','barrelImpulseY') if k in stage}})
        ids = [o['id'] for o in module['InteractableObject']]
        if stage['coins']:
            line, role, start, end = coin_line(module, stage)
            locations = distribute(line, stage['coins'], start, end)
            for px,py in locations:
                coin_id = f'coin_{len(coins)+1:02d}'
                coins.append({'id':coin_id, 'type':'coin', 'transform':{'x':px,'y':py},
                    'properties':{'optional':True, 'beatId':stage['beat'], 'line':role,
                        'collectionRisk':'optional_stunt' if role != 'crest_or_flow' else 'flow',
                        'validationStatus':'geometry_checked; collectibility needs Unity bike test'}})
                ids.append(coin_id)
        cp_id = None
        if stage.get('checkpoint'):
            if module['jumpUnit']:
                cp_x = module['jumpUnit']['checkpointCandidateAfterStabilization']
            else:
                cp_x = stop['x']-3
            cp_id = 'cp_'+stage['beat']
            checkpoints.append({'id':cp_id, 'transform':{'x':cp_x,'y':stop['y']+1,'rotation':0},
                'metadata':{'afterBeat':stage['beat'], 'expectedRestartSpeed':0,
                    'purpose':'First stable pad after challenge resolution',
                    'resetExplosiveAssemblies':True,'requiresRestartTest':True}})
        feature = [pt for line in road_lines(module) for pt in line]
        feature += [pt for shape in module['RampPlatform'] for pt in sample_curve(shape)]
        feature += [(c['transform']['x'],c['transform']['y']) for c in coins if c['properties']['beatId']==stage['beat']]
        for obj in module['InteractableObject']:
            if obj['type']=='SpringObject':
                feature += spring_trajectory(obj)
            elif obj['type']=='explosive_barrel':
                feature += [(p['x'],p['y']) for p in obj['properties']['previewTrajectory']['points']]
        xs, ys = zip(*feature)
        unit = module['jumpUnit']
        recovery = unit['recovery'] if unit else [max(x,stop['x']-8),stop['x']]
        if stage.get('barrel'):
            landing = next(o['properties']['landingPosition'] for o in module['InteractableObject'] if o['type']=='explosive_barrel')
            recovery = [landing['x'],stop['x']]
        beats.append({'id':stage['beat'],'role':stage['role'],'tension':stage['tension'],
            'purpose':stage['purpose'],'xRange':[x,stop['x']],'objectIds':ids,'coinCount':stage['coins'],
            'checkpointId':cp_id, 'recoveryRange':recovery,
            'cameraCue':'Reveal destination before commitment, track apex, recenter during recovery',
            'camera':{'triggerX':max(0,x-12), 'recenterX':recovery[0]+3,
                'framingBounds':{'minX':min(xs)-3,'maxX':max(xs)+3,'minY':min(ys)-2,'maxY':max(ys)+3},
                'implementationStatus':'authored intent; Unity camera implementation pending'},
            'fallback':'continuous ground below optional ramp' if module['RampPlatform'] else
                       'deadzone catches missed crossing; restart at previous checkpoint' if unit else 'continuous ground',
            'validationStatus':'static geometry and idealized arcs; Unity playtests pending'})
        x,y = stop['x'],stop['y']
    level = as_level(modules, f"campaign_{recipe['number']:02d}", recipe['name'], preview=True)
    level['InteractableObject'].extend(coins)
    level['map'].update(previewOnly=False, authoringMode='curated_catalog_composition',
                        catalogVersion='2.1', recipe='library/campaign_recipes.json',
                        recipeNumber=recipe['number'], referenceLevel='levels/00_cinder_crown.json',
                        subtitle=recipe['signature'])
    end_x = level['End']['x']
    finish_margin = max(8, (end_x-level['Start']['x'])*.08)
    level['CheckPoint'] = [cp for cp in checkpoints if end_x-cp['transform']['x']>=finish_margin]
    kept_cp = {cp['id'] for cp in level['CheckPoint']}
    for beat in beats:
        if beat['checkpointId'] and beat['checkpointId'] not in kept_cp:
            beat['checkpointId']=None
            beat['checkpointOmittedBecause']='Within finish release margin'
    level['design'].update(intent=recipe['intent'], signature=recipe['signature'],
        difficulty=recipe['difficulty'], sequence=sequence, cinematicBeats=beats,
        coinCount=20, coinAllocation={s['beat']:s['coins'] for s in stages if s['coins']},
        length=end_x-level['Start']['x'],
        validationStatus='geometry_checked; Unity bike playtests pending',
        recommendedPlaytestOrder=['Main route with no optional stunts',
            'Zero-speed restart at every checkpoint', 'Loop with zero/one/two boosts where present',
            'Spring lip trigger, descending catch and rearming',
            'Barrel at low/typical/high entry speed', 'All 20 coins and per-beat camera framing'])
    # The median uses uniform ground-X samples, excluding voids and export padding.
    road = [pt for module in modules for line in road_lines(module)
            for pt in [(px,height_at(line,px)) for px in
                       [line[0][0]+j*.25 for j in range(int((line[-1][0]-line[0][0])/.25)+1)]]]
    middle = median(py for px,py in road)
    bx, by = min(road, key=lambda p: abs(p[1]-middle))
    level['Bottom']={'x':bx,'y':by}
    visible = [pt for module in modules for line in road_lines(module) for pt in line]
    for group in ('RampPlatform','InteractableObject'):
        for obj in level[group]:
            if 'points' in obj:
                visible += sample_curve(obj, 100)
            elif obj.get('type')=='SpringObject':
                visible += spring_trajectory(obj, 200)
            elif obj.get('type')=='explosive_barrel':
                visible += [(p['x'],p['y']) for p in obj['properties']['previewTrajectory']['points']]
            if 'transform' in obj:
                visible.append((obj['transform']['x'],obj['transform']['y']))
    tx,ty=max(visible,key=lambda p:p[1])
    level['Top']={'x':tx,'y':ty}
    return level


def validate_authored(source, exported):
    """Fail generation on broken authoring/export contracts; record useful checks."""
    errors=[]
    def require(condition, message):
        if not condition:
            errors.append(message)
    objects=exported['InteractableObject']
    coins=[o for o in objects if o['type']=='coin']
    require(len(coins)==20, 'Expected exactly 20 coins')
    ids=[o['id'] for g in GROUPS+('CheckPoint',) for o in exported[g]]
    require(len(ids)==len(set(ids)), 'Object/shape IDs must be unique')
    for i, a in enumerate(coins):
        for b in coins[i+1:]:
            require(math.dist(tuple(a['transform'][k] for k in ('x','y')),
                              tuple(b['transform'][k] for k in ('x','y')))>=1.2,
                    f"Coins too close: {a['id']}/{b['id']}")
    require(source['Start']==exported['Start'] and source['End']==exported['End'], 'Markers moved during export')
    require(export_level(exported)==exported, 'Export is not idempotent')
    terrain=exported['MainPlatform']
    require(len({p['y'] for s in terrain for p in s['points'][-2:]})==1, 'Terrain floors differ')
    require(terrain[0]['points'][0]['x']==-20, 'Left padding missing')
    end_surface=terrain[-1]['points'][surface_count(terrain[-1])-1]
    require(abs(end_surface['x']-(source['End']['x']+21))<1e-6, 'Right padding missing')
    lines=road_lines(exported)
    for s in terrain:
        require(s['closed'], 'Main terrain must be closed')
        surface_count(s)
        for p in s['points']:
            require(all(k in p for k in ('tangentIn','tangentOut','tangentMode','corner')), 'Missing SpriteShape tangent data')
    for s in exported['RampPlatform']:
        require(not s['closed'], 'Auxiliary ramp must be open')
    gap_checks=[]
    for left,right in zip(lines,lines[1:]):
        a,b=left[-1][0],right[0][0]
        if b-a <= 1e-5:
            require(False,'Touching ground was not merged')
            continue
        covering=[z for z in exported['deadzone'] if
                  min(p['x'] for p in z['points'])<=a and max(p['x'] for p in z['points'])>=b]
        require(bool(covering),f'Unprotected void [{a}, {b}]')
        for z in covering:
            require(max(p['x'] for p in z['points'])-min(p['x'] for p in z['points'])<=b-a+.21,
                    'Deadzone is wider than the gap')
            require(max(p['y'] for p in z['points'])-min(p['y'] for p in z['points'])<=1.51,
                    'Deadzone is too tall')
        gap_checks.append({'xRange':[a,b],'covered':bool(covering)})
    margin=max(8,(exported['End']['x']-exported['Start']['x'])*.08)
    for cp in exported['CheckPoint']:
        x,y=cp['transform']['x'],cp['transform']['y']
        line=next((line for line in lines if line[0][0]<=x<=line[-1][0]),None)
        require(line is not None, 'Checkpoint over void')
        if line:
            require(abs(height_at(line,x)+1-y)<.01, 'Checkpoint not on the ground')
            require(abs(height_at(line,x-1)-height_at(line,x+1))<.02, 'Checkpoint not on stable flat pad')
        require(exported['End']['x']-x>=margin, 'Checkpoint too close to End')
    for a,b in zip(source['design']['sequence'],source['design']['sequence'][1:]):
        require(abs(a['xRange'][1]-b['xRange'][0])<1e-6 and abs(a['exitY']-b['entryY'])<1e-6,
                'Obstacle port seam')
    springs=[]
    for obj in objects:
        props=obj.get('properties',{})
        if 'postDestroyRoute' in props:
            require(props['postDestroyRoute'] in ids, 'Broken fallback reference after merge')
        if 'supportingShapeId' in props:
            require(props['supportingShapeId'] in ids, 'Broken boost support reference')
        if obj['type']=='SpringObject':
            arc=spring_trajectory(obj,200)
            target=props['targetPosition']
            require(target['y']>obj['transform']['y'], 'Spring destination must be higher')
            require(arc[-1][1]<arc[-2][1], 'Spring must arrive descending')
            for px,py in arc:
                heights=[height_at(line,px) for line in lines]
                heights=[h for h in heights if h is not None]
                if heights:
                    require(py-max(heights)>=.99,'Spring arc intersects terrain before target')
                for z in exported['deadzone']:
                    xs=[p['x'] for p in z['points']]; ys=[p['y'] for p in z['points']]
                    require(not(min(xs)<=px<=max(xs) and min(ys)<=py<=max(ys)), 'Spring arc intersects deadzone')
            springs.append({'id':obj['id'],'descendingCatch':True,'terrainClearance':'reference point >= 1 unit'})
    jump_checks=[]
    for obstacle in source['design']['obstacles']:
        check=obstacle['physics'].get('pointMassCheck')
        if check:
            central=[r for r in check['sweep'] if r['multiplier'] in (.9,1,1.1)]
            require(len(central)==3 and all(r.get('landingQuality')=='aligned' for r in central),
                    f"{obstacle['id']}: catch must accommodate 90/100/110 percent target speed")
            jump_checks.append({'variant':obstacle['id'], 'coordinateSpace':'module_local',
                'alignedSpeedMultipliers':[r['multiplier'] for r in central if r.get('landingQuality')=='aligned'],
                'status':'point_mass_only'})
        for join in obstacle['joins']:
            first=next(o for o in objects if o['id']==join['from'])['points'][join['fromPoint']]
            second=next(o for o in exported['RampPlatform'] if o['id']==join['to'])['points'][join['toPoint']]
            require(math.hypot(first['x']-second['x'],first['y']-second['y'])<1e-6, 'Loop join position seam')
            a,b=first['tangentIn'],second['tangentOut']
            require(abs(a['x']*b['y']-a['y']*b['x'])<1e-5 and a['x']*b['x']+a['y']*b['y']<0,
                    'Loop join tangent seam')
    if errors:
        raise ValueError(exported['map']['name']+': '+ '; '.join(sorted(set(errors))))
    return {'status':'passed_static_geometry_and_point_mass_checks',
            'sourceMainShapes':len(source['MainPlatform']),'exportedMainShapes':len(terrain),
            'coinCount':len(coins),'coveredGaps':gap_checks,'springChecks':springs,
            'jumpChecks':jump_checks,
            'checkpointCount':len(exported['CheckPoint']),
            'exportIdempotent':True,'unchangedStartEnd':True,
            'remainingValidation':'Unity bike size, torque, suspension, restart, impulse, coin pickup and camera playtests'}


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
    recipes = json.loads((ROOT/'library/campaign_recipes.json').read_text(encoding='utf-8'))
    authored = {r['number']: r for r in recipes['maps']}
    families = load_catalog()
    levels = []
    for n in range(1, args.count+1):
        source = build_authored_level(authored[n], families) if n in authored else build_campaign_level(n)
        level = export_level(source)
        if n in authored:
            level['design']['validationReport'] = validate_authored(source, level)
        levels.append(level)
    args.output.mkdir(parents=True, exist_ok=True)
    for path, level in zip(paths, levels):
        path.write_text(json.dumps(level, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    manifest = {"count": len(levels), "catalogVersion": "2.1", "recipeVersion":recipes['version'],
                "validationStatus": "geometry_only; Unity playtests pending",
                "maps": [{"id": level["map"]["id"], "file": path.name,
                          "name": level['map']['name'],
                          "intent": level['design'].get('intent'),
                          "signature": level['design'].get('signature'),
                          "difficulty": level['design'].get('difficulty'),
                          "variants": level["design"]["variants"]}
                         for path, level in zip(paths, levels)]}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {len(levels)} catalog-based maps in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
