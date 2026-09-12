"""Curated showcase level assembled from authored catalog variants.

Run again with --overwrite to rebuild this map after changing its recipe.
"""
import argparse
import copy
import json
import math
from pathlib import Path

from obstacle_library import ROOT, load_catalog, build_variant, place, as_level, sample_curve, profile, ground


def flight_hit(module, speed):
    unit = module['jumpUnit']
    launch = module['MainPlatform'][0]
    tip = launch['points'][launch['metadata']['drivingSurfaceCount']-1]
    landing = module['MainPlatform'][1]
    n = landing['metadata']['drivingSurfaceCount']
    line = sample_curve({'points':landing['points'][:n], 'closed':False}, steps=160)
    angle = math.radians(unit['launchAngle'])
    vx = speed*math.cos(angle)
    k = 9.81/(2*vx*vx)
    def flight_y(x):
        dx=x-tip['x']
        return tip['y']+math.tan(angle)*dx-k*dx*dx
    if flight_y(line[0][0]) < line[0][1]:
        return {'result':'undershoot'}
    for (x1,y1),(x2,y2) in zip(line,line[1:]):
        if flight_y(x1)>=y1 and flight_y(x2)<=y2:
            lo,hi=x1,x2
            slope=(y2-y1)/(x2-x1)
            for _ in range(25):
                mid=(lo+hi)/2
                if flight_y(mid) > y1+slope*(mid-x1):
                    lo=mid
                else:
                    hi=mid
            x=(lo+hi)/2
            vy=speed*math.sin(angle)-9.81*(x-tip['x'])/vx
            impact_angle=math.degrees(math.atan2(vy,vx))
            return {'result':'catch' if x<=unit['landing'][1] else 'late_flat_impact',
                    'x':round(x,3),'y':round(flight_y(x),3),
                    'airtime':round((x-tip['x'])/vx,3),
                    'pitchMismatchDegrees':round(abs(impact_angle-math.degrees(math.atan(slope))),2)}
    return {'result':'overshoot'}


def tune_flight(module):
    # Bespoke catch for this showcase: preserve a steep touchdown face before
    # easing out into the stabilization pad. Library parameters remain intact.
    u=module['jumpUnit']
    old=module['MainPlatform'][1]
    lx,ly=old['points'][0]['x'],old['points'][0]['y']
    slope=-1.55 if module['id']=='gap.trick_gap' else -.95
    face_length=3.5
    end_y=ly+slope*(face_length+2)
    surface=profile([(lx,ly),(lx+face_length,ly+slope*face_length),
                     (lx+face_length+4,end_y),(u['landing'][1],end_y),
                     (u['recovery'][1],end_y)],{0:slope,1:slope,2:0})
    module['MainPlatform'][1]=ground(old['id'],surface)
    u['landing'][1]=lx+face_length+4
    u['recovery'][0]=u['landing'][1]
    u['checkpointCandidateAfterStabilization']=u['landing'][1]+3
    module['ports']['exit']['y']=end_y
    module['groundSamples']=[]
    for shape in module['MainPlatform']:
        module['groundSamples'].extend(sample_curve({'points':shape['points'][:shape['metadata']['drivingSurfaceCount']], 'closed':False}))
    module['physics']['showcaseOverride']='Extended sloped touchdown face, then smooth recovery; source catalog unchanged'
    window=module['physics']['entrySpeedWindow']
    baseline=window['ideal']
    target=module['jumpUnit']['landing'][0]+2
    trials=[]
    for i in range(181):
        speed=baseline*(.7+i/300)
        hit=flight_hit(module,speed)
        if hit['result']=='catch':
            trials.append((abs(hit['x']-target)+.6*hit['pitchMismatchDegrees'],speed,hit))
    if not trials:
        raise ValueError('No point-mass catch for '+module['id'])
    _,ideal,best=min(trials)
    window.update({'minimum':round(min(t[1] for t in trials),3),'ideal':round(ideal,3),
                   'maximum':round(max(t[1] for t in trials),3),'status':'sampled_point_mass_only'})
    report={'obstacle':module['id'],'ideal':window['ideal'],'idealCatch':best,
            'limitations':'No bike size, torque, air input, suspension, wheel contact or boost simulation.',
            'coordinateSpace':'module local',
            'sweep':[{'multiplier':factor,'speed':round(ideal*factor,3),**flight_hit(module,ideal*factor)} for factor in (.8,.9,1,1.1,1.2)]}
    for row in report['sweep']:
        row['landingQuality']='aligned' if row.get('pitchMismatchDegrees',999)<15 else 'hard_impact' if 'x' in row else 'miss'
    tip=module['MainPlatform'][0]['points'][-3]
    angle=math.radians(module['jumpUnit']['launchAngle'])
    module['coinCandidates']=[]
    for f in (.22,.5,.78):
        dx=f*(module['jumpUnit']['flight'][1]-tip['x'])
        module['coinCandidates'].append({'x':tip['x']+dx,'y':tip['y']+math.tan(angle)*dx-9.81*dx*dx/(2*(ideal*math.cos(angle))**2)+1,
                                          'role':'flight_arc','priority':6})
    return report


def build():
    recipe=json.loads((ROOT/'designs/cinder_crown.recipe.json').read_text(encoding='utf-8'))
    families=load_catalog()
    modules=[]
    reports=[]
    x=y=0
    for i,stage in enumerate(recipe['stages']):
        family=families[stage['variant'].split('.')[0]]
        variant=next(v for v in family['variants'] if v['id']==stage['variant'])
        local=build_variant(family,variant)
        if local['jumpUnit']:
            reports.append(tune_flight(local))
            # Treat the first long gap as a meaningful checkpoint challenge too.
            u=local['jumpUnit']
            local['checkpointCandidates']=[{'x':u['landing'][1]+3,'y':local['ports']['exit']['y']+1,
                                            'expectedRestartSpeed':0,'requiresRestartTest':True}]
        module=place(local,x,y,f's{i+1:02d}')
        stage['xRange']=[x,module['ports']['exit']['x']]
        stage['entryY']=y
        modules.append(module)
        x,y=module['ports']['exit']['x'],module['ports']['exit']['y']
    loop=modules[4]
    loop['checkpointCandidates']=[{'x':loop['ports']['exit']['x']-4,'y':loop['ports']['exit']['y']+1,
                                  'expectedRestartSpeed':0,'requiresRestartTest':True}]
    # Three coins INSIDE the loop surface track bike contact; outside coins
    # would incorrectly suggest jumping off the loop at its apex.
    cx=loop['ports']['entry']['x']+21
    cy=loop['ports']['entry']['y']+5
    loop['coinCandidates']=[{'x':cx+2.15*math.cos(math.radians(a)),
                            'y':cy+2.15*math.sin(math.radians(a)),
                            'priority':7,'role':'optional_loop_inside'} for a in (15,90,165)]
    level=as_level(modules,recipe['id'],recipe['name'])
    chosen=modules[2]['coinCandidates']+loop['coinCandidates']+modules[6]['coinCandidates']+modules[7]['coinCandidates']
    assert len(chosen)==10
    level['InteractableObject']=[o for o in level['InteractableObject'] if o['type']!='coin']
    for i,c in enumerate(chosen):
        level['InteractableObject'].append({'id':f'coin_{i+1:02d}','type':'coin','transform':{'x':round(c['x'],5),'y':round(c['y'],5)},
                                            'properties':{'optional':True,'line':c['role'],'collectionRisk':'medium' if 'loop' not in c['role'] else 'high'}})
    # Boost is confined to the optional loop entry; no forced barrel impulse.
    level['InteractableObject'].append({'id':'boost_crown_entry','type':'speed_boost',
        'transform':{'x':loop['ports']['entry']['x']+10,'y':loop['ports']['entry']['y']+.85},
        'properties':{'durationSeconds':3,'stackable':True,'speedMultiplier':1.12,'purpose':'loop entry assistance; calibrate with bike physics'}})
    for item in level['InteractableObject']:
        if item['type']=='explosive_ramp':
            item['properties']['optionalRoute']=True
            item['properties']['colliderDisableEvent']='rear wheel exits connector; remove supporting surface only after clearance'
    level['map'].update({'subtitle':'Quarry crossing / explosive crown / last-light flight',
                         'authoringMode':'curated_catalog_composition','recipe':'designs/cinder_crown.recipe.json'})
    # Bottom uses median over equal-X ground samples, not control-point count.
    ground_samples=[p for m in modules for p in m['groundSamples']]
    bins={}
    for a,b in ground_samples:
        bins.setdefault(round(a),[]).append(b)
    from statistics import median
    bottom_y=median(median(values) for values in bins.values())
    bottom=min(ground_samples,key=lambda p:abs(p[1]-bottom_y))
    level['Bottom']={'x':bottom[0],'y':bottom[1]}
    level['design'].update({'stages':recipe['stages'],'difficulty':'medium-hard; loop optional','length':round(x,3),
        'coinAllocation':recipe['coinAllocation'],'estimatedDurationSeconds':[30,45],
        'durationStatus':'design target, not measured',
        'cameraPlan':{'lookAheadDistance':32,'loopViewHeight':14,'visibility':'show each landing before launch; zoom out before crown'},
        'pointMassChecks':reports,'playtestStatus':'pending Unity bike physics',
        'replayGoal':'learn the two main jumps; optional loop rewards three coins; finish remains unobstructed',
        'recommendedPlaytestOrder':['main route without boost','checkpoint restart at zero speed','loop with booster','rear-wheel-clear destruction and checkpoint reset','all ten coins on one run']})
    verify(level,modules)
    return level


def verify(level,modules):
    from level_visualizer import validate
    assert not [i for i in validate(level,.01) if i.severity=='error']
    assert sum(o['type']=='coin' for o in level['InteractableObject'])==10
    assert len(level['CheckPoint'])==3
    minimum=max(8,.08*(level['End']['x']-level['Start']['x']))
    for cp in level['CheckPoint']:
        assert level['End']['x']-cp['transform']['x']>=minimum
    for a,b in zip(modules,modules[1:]):
        assert a['ports']['exit']==b['ports']['entry']
    ids=[i['id'] for g in ('MainPlatform','RampPlatform','deadzone','InteractableObject','CheckPoint') for i in level[g]]
    assert len(ids)==len(set(ids))
    assert all(r['idealCatch']['result']=='catch' for r in level['design']['pointMassChecks'])


def render_review(level,path):
    from matplotlib.figure import Figure
    from matplotlib.patches import Polygon
    fig=Figure(figsize=(18,12),facecolor='#101923',layout='constrained')
    axes=fig.subplots(4,1,gridspec_kw={'height_ratios':[.65,1,1,1]})
    stages=level['design']['stages']
    ranges=[(0,level['design']['length'])]+[(min(s['xRange'][0] for s in stages if s['act']==act),max(s['xRange'][1] for s in stages if s['act']==act)) for act in (1,2,3)]
    titles=['FULL ROUTE  /  307 units  /  10 coins  /  3 checkpoints',
            '01  QUARRY CROSSING  |  Build speed, commit, recover',
            '02  THE CROWN  |  Optional loop, explosive connector, ground fallback',
            '03  LAST LIGHT  |  Two rollers, trick flight, clear runout']
    for index,(ax,(left,right)) in enumerate(zip(axes,ranges)):
        ax.set_facecolor('#101923')
        for group,color in (('deadzone','#d55966'),('MainPlatform','#42ad88'),('RampPlatform','#f4b348'),('InteractableObject','#ee6555')):
            for item in level[group]:
                if 'points' not in item:
                    continue
                line=sample_curve(item)
                if item.get('closed'):
                    ax.add_patch(Polygon(line,facecolor=color,edgecolor=color,alpha=.6))
                else:
                    ax.plot(*zip(*line),color=color,lw=2.8)
        for obj in level['InteractableObject']:
            if 'transform' in obj:
                p=obj['transform']
                ax.scatter(p['x'],p['y'],s=38 if obj['type']=='coin' else 70,color='#ffdc73' if obj['type']=='coin' else '#64bafa',marker='o' if obj['type']=='coin' else '>',zorder=5)
        for i,cp in enumerate(level['CheckPoint']):
            p=cp['transform']
            ax.scatter(p['x'],p['y'],marker='P',color='#c9a6ff',s=85,zorder=6)
            if index and left<=p['x']<=right:
                ax.annotate(f'CP {i+1}',(p['x'],p['y']),xytext=(0,-22),textcoords='offset points',color='#d0b6ff',ha='center',fontsize=9)
        for label,color in (('Start','#8deac0'),('End','#f59286')):
            p=level[label]
            if left<=p['x']<=right:
                ax.axvline(p['x'],color=color,lw=.8,alpha=.6)
                ax.text(p['x'],.9,label.upper(),color=color,fontsize=9,transform=ax.get_xaxis_transform())
        ax.set_xlim(left-3,right+3)
        relevant=[p for group in ('MainPlatform','RampPlatform') for shape in level[group] for p in sample_curve(shape) if left<=p[0]<=right]
        ax.set_ylim(min(y for x,y in relevant)-3,max(y for x,y in relevant)+5)
        ax.set_aspect('equal',adjustable='box')
        ax.set_title(titles[index],loc='left',color='#f2f5f8',fontsize=11,pad=10)
        ax.grid(alpha=.09,color='white')
        ax.tick_params(colors='#91a4b6',labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#293d50')
    fig.suptitle('C I N D E R   C R O W N\nCurated stunt-bike track / catalog v2 / geometry + point-mass preview',color='#f3f1e9',fontsize=19)
    fig.savefig(path,dpi=150,facecolor=fig.get_facecolor())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--overwrite',action='store_true')
    args=parser.parse_args()
    output=ROOT/'levels/00_cinder_crown.json'
    if output.exists() and not args.overwrite:
        parser.error('Map exists; use --overwrite to rebuild this authored map')
    level=build()
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(level,indent=2),encoding='utf-8')
    preview=ROOT/'designs/cinder_crown.preview.png'
    render_review(level,preview)
    print(f'Level: {output}\nPreview: {preview}')
    print('Length:',level['design']['length'],'coins: 10; checkpoints: 3')
    for r in level['design']['pointMassChecks']:
        print(r['obstacle'],'ideal',r['ideal'],'catch',r['idealCatch'])


if __name__=='__main__':
    main()
