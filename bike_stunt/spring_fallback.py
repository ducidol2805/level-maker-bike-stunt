"""A missed spring drops onto a slower road beneath the floating catch."""
import copy

from bike_stunt.obstacle_library import ground, linear_polygon, profile
from bike_stunt.terrain_export import are_opposite_tangents, surface_count


def merge_approach(approach, road):
    """Author one terrain body, preserving both handles at the spring lip."""
    left = copy.deepcopy(approach['points'][:surface_count(approach)])
    right = copy.deepcopy(road['points'][:surface_count(road)])
    if (left[-1]['x'], left[-1]['y']) != (right[0]['x'], right[0]['y']):
        raise ValueError('Connected spring platforms must share an endpoint')
    left[-1]['tangentOut'] = right[0]['tangentOut']
    left[-1]['tangentMode'] = ('continuous' if are_opposite_tangents(
        left[-1]['tangentIn'], left[-1]['tangentOut']) else 'broken')
    merged = ground(approach['id'], left + right[1:])
    merged['metadata']['role'] = 'spring_approach_tunnel_and_recovery'
    return merged


def build_tunnel(lip, launch_y, landing_coords, parameters):
    """Keep the authored upper landing; open a rideable tunnel underneath it.

    The ceiling copies the lower road's cubic handles with a vertical offset,
    so clearance is constant along the entire tunnel, not just at its knots.
    """
    landing_x, landing_y = landing_coords[0]
    end_x, end_y = landing_coords[-1]
    catch_end = landing_x + parameters['landing']
    roof_end = catch_end + parameters['recovery'] * .15
    rejoin_x = end_x - parameters['recovery'] * .3
    clearance = 3.0
    roof_floor_y = min(y for _, y in landing_coords) - clearance - 2
    low_y = min(launch_y - 3, roof_floor_y - 1)
    rise_start = landing_x + min(parameters['landing'] * .55,
                                max(parameters['targetInset'] + 1, parameters['landing'] * .3))
    connected = parameters['platformConnection'] == 'connected'
    lower_start = lip if connected else lip + parameters['gap']*.55
    entry = ([(lip, launch_y), (lip+parameters['gap']*.5, low_y)]
             if connected else [(lower_start, low_y)])
    lower = profile(entry + [(landing_x, low_y),
                     (rise_start, low_y + (roof_floor_y-low_y)*.25),
                     (catch_end, roof_floor_y), (roof_end, roof_floor_y),
                     (rejoin_x, end_y), (end_x, end_y)])
    road = ground('spring_lower_recovery', lower)
    road['metadata']['role'] = 'missed_spring_tunnel_and_climb'
    upper = profile(landing_coords[:-1] + [(roof_end, end_y)])
    # Reverse both knot order and handle direction when walking the underside.
    ceiling = []
    for knot in reversed([p for p in lower if landing_x-1e-6 <= p['x'] <= roof_end+1e-6]):
        knot = copy.deepcopy(knot)
        knot['y'] += clearance
        knot['tangentIn'], knot['tangentOut'] = knot['tangentOut'], knot['tangentIn']
        ceiling.append(knot)
    upper[0]['tangentIn'] = {'x': 0, 'y': 0}
    upper[-1]['tangentOut'] = {'x': 0, 'y': 0}
    ceiling[0]['tangentIn'] = {'x': 0, 'y': 0}
    ceiling[-1]['tangentOut'] = {'x': 0, 'y': 0}
    roof = {'id': 'spring_landing_recovery', 'closed': True,
            'points': upper + ceiling,
            'metadata': {'drivingSurfaceCount': len(upper), 'role': 'spring_catch_and_tunnel_roof'}}
    # The common deadzone offset is still applied once by the variant builder.
    kill_y = min(launch_y, low_y) - 1.5
    zone = None if connected else linear_polygon('spring_gap_kill',
                          [(lip-.1, kill_y), (lower_start+.1, kill_y),
                           (lower_start+.1, kill_y-1.5), (lip-.1, kill_y-1.5)])
    return road, roof, zone, lower
