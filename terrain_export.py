"""Finalize closed terrain cross-sections without tessellating Bezier surfaces.

Authoring retains separate obstacles; export removes only shared terrain seams.
"""
import argparse
import copy
import json
import math
from pathlib import Path


def surface_count(shape):
    points = shape['points']
    metadata = shape.get('metadata', {})
    count = metadata.get('drivingSurfaceCount', len(points)-2)
    if not shape.get('closed') or count < 2 or count != len(points)-2:
        raise ValueError(f"{shape.get('id')}: expected a surface followed by two bottom corners")
    right, left = points[-2:]
    if not metadata.get('freeBottomCorners') and (abs(right['x']-points[count-1]['x']) > 1e-6 or
            abs(left['x']-points[0]['x']) > 1e-6 or
            max(right['y'], left['y']) >= min(p['y'] for p in points[:count])):
        raise ValueError(f"{shape.get('id')}: unsupported terrain closure; cannot safely infer bottom corners")
    if any(a['x'] > b['x'] for a, b in zip(points[:count], points[1:count])):
        raise ValueError(f"{shape.get('id')}: driving surface must run left to right")
    return count


def normalize_main_platform(level):
    """Normalize generated undersides while preserving freely edited corners.

    The gameplay Bottom marker (median ground height) is deliberately unchanged.
    """
    result = copy.deepcopy(level)
    shapes = result.get('MainPlatform', [])
    for shape in shapes:
        surface_count(shape)
    generated = [s for s in shapes if not s.get('metadata', {}).get('freeBottomCorners')]
    if generated:
        floor = min(p['y'] for s in generated for p in s['points'][-2:])
        for shape in generated:
            for p in shape['points'][-2:]:
                p.update(y=floor, tangentIn={'x': 0, 'y': 0},
                         tangentOut={'x': 0, 'y': 0}, tangentMode='linear')
    return result


def extend_boundaries(level, distance):
    """Add flat ground outside the route, without moving any gameplay markers.

    Store applied padding so re-exporting does not extend the map repeatedly.
    """
    if not math.isfinite(distance) or distance < 0:
        raise ValueError('boundary padding must be finite and non-negative')
    shapes = level.get('MainPlatform', [])
    if not shapes:
        return
    applied = level.get('terrainExport', {}).get('boundaryPadding', 0)
    if distance < applied:
        raise ValueError('Cannot shrink exported padding; export from the authoring source')
    extra = distance - applied
    if not extra:
        return
    for shape, is_left in ((shapes[0], True), (shapes[-1], False)):
        n = surface_count(shape)
        surface = shape['points'][:n]
        edge = surface[0 if is_left else -1]
        # Preserve the road-facing handle. Only the old closure-facing handle
        # becomes a flat extension; do not stretch any authored road segment.
        if edge.get('tangentMode') == 'linear':
            edge['tangentIn'] = {'x': 0, 'y': 0}
            edge['tangentOut'] = {'x': 0, 'y': 0}
        edge['tangentIn' if is_left else 'tangentOut'] = {'x': 0, 'y': 0}
        edge['tangentMode'] = 'broken'
        x = edge['x'] + (-extra if is_left else extra)
        new = {'x': x, 'y': edge['y'], 'tangentIn': {'x': 0, 'y': 0},
               'tangentOut': {'x': 0, 'y': 0}, 'tangentMode': 'linear', 'corner': False}
        surface = [new] + surface if is_left else surface + [new]
        if not shape.get('metadata', {}).get('freeBottomCorners'):
            shape['points'][-1 if is_left else -2]['x'] = x
        shape['points'] = surface + shape['points'][-2:]
        shape.setdefault('metadata', {})['drivingSurfaceCount'] = n+1
    level.setdefault('terrainExport', {})['boundaryPadding'] = distance


def extend_straight_join_handles(shape):
    """Give a collapsed straight-side handle the opposite road tangent.

    Only collinear straight segments qualify. Limit the new handle to a third
    of that segment so short flats cannot overshoot their adjacent vertex.
    """
    points = shape['points'][:surface_count(shape)]
    epsilon = 1e-8

    def handle(point, key):
        value = point.get(key, {}) if point.get('tangentMode') != 'linear' else {}
        return value.get('x', 0), value.get('y', 0)

    for i in range(1, len(points)-1):
        point = points[i]
        if point.get('corner') or point.get('tangentMode') == 'linear':
            continue
        incoming, outgoing = handle(point, 'tangentIn'), handle(point, 'tangentOut')
        in_length, out_length = math.hypot(*incoming), math.hypot(*outgoing)
        if (in_length <= epsilon) == (out_length <= epsilon):
            continue
        missing_in = in_length <= epsilon
        existing = outgoing if missing_in else incoming
        length = math.hypot(*existing)
        direction = (-existing[0]/length, -existing[1]/length)
        neighbor = points[i-1 if missing_in else i+1]
        chord = (neighbor['x']-point['x'], neighbor['y']-point['y'])
        span = math.hypot(*chord)
        if span <= epsilon:
            continue
        # The new handle must remain on the straight road, not round a ledge.
        if (abs(direction[0]*chord[1]-direction[1]*chord[0]) > span*1e-6 or
                direction[0]*chord[0]+direction[1]*chord[1] <= 0):
            continue
        far_handle = handle(neighbor, 'tangentOut' if missing_in else 'tangentIn')
        if abs(far_handle[0]*chord[1]-far_handle[1]*chord[0]) > span*1e-6:
            continue
        extension = min(length, span/3)
        point['tangentIn' if missing_in else 'tangentOut'] = {
            'x': direction[0]*extension, 'y': direction[1]*extension}
        point['tangentMode'] = 'continuous'


def are_opposite_tangents(incoming, outgoing, epsilon=1e-8):
    """Return whether two active handles describe one smooth tangent line."""
    in_x, in_y = incoming.get('x', 0), incoming.get('y', 0)
    out_x, out_y = outgoing.get('x', 0), outgoing.get('y', 0)
    in_length = math.hypot(in_x, in_y)
    out_length = math.hypot(out_x, out_y)
    if in_length <= epsilon or out_length <= epsilon:
        return False
    # Collinear handles must point away from the shared control point.
    cross = in_x * out_y - in_y * out_x
    dot = in_x * out_x + in_y * out_y
    return abs(cross) <= epsilon * in_length * out_length and dot < 0


def export_level(level, tolerance=1e-6, *, boundary_padding=None):
    """Merge endpoint-connected terrain chains, never bridge real gaps.

    Preserve incoming/outgoing Bezier handles at each shared road vertex.
    Keep the first shape ID and remap fallback references to surviving IDs.
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('tolerance must be finite and non-negative')
    if boundary_padding is None:
        boundary_padding = level.get('terrainExport', {}).get('boundaryPadding', 20)
    result = normalize_main_platform(level)
    shapes = sorted(result.get('MainPlatform', []), key=lambda s: s['points'][0]['x'])
    merged, id_map = [], {}
    for shape in shapes:
        sid = shape['id']
        if merged:
            previous = merged[-1]
            n, m = surface_count(previous), surface_count(shape)
            a, b = previous['points'][n-1], shape['points'][0]
            # Overlap is not an endpoint join; it needs a polygon-union workflow.
            join = (not previous.get('metadata', {}).get('freeBottomCorners') and
                    not shape.get('metadata', {}).get('freeBottomCorners') and
                    b['x'] >= a['x'] and math.hypot(a['x']-b['x'], a['y']-b['y']) <= tolerance)
            if join:
                # A linear endpoint has no active Bezier handles.
                incoming = a.get('tangentIn', {}) if a.get('tangentMode') != 'linear' else {}
                outgoing = b.get('tangentOut', {}) if b.get('tangentMode') != 'linear' else {}
                tangent_mode = ('continuous'
                                if not a.get('corner') and not b.get('corner') and
                                are_opposite_tangents(incoming, outgoing)
                                else 'broken')
                a.update(tangentIn={'x': incoming.get('x', 0), 'y': incoming.get('y', 0)},
                         tangentOut={'x': outgoing.get('x', 0), 'y': outgoing.get('y', 0)},
                         tangentMode=tangent_mode)
                previous['points'] = previous['points'][:n] + shape['points'][1:m] + [shape['points'][-2], previous['points'][-1]]
                meta = previous.setdefault('metadata', {})
                meta['drivingSurfaceCount'] = n+m-1
                sources = meta.setdefault('sourceShapeIds', [previous['id']])
                sources.extend(shape.get('metadata', {}).get('sourceShapeIds', [sid]))
                id_map[sid] = previous['id']
                continue
        merged.append(shape)
        id_map[sid] = sid
    result['MainPlatform'] = merged
    # References to the ground route must survive merging (explosive loop).
    def remap(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'postDestroyRoute' and isinstance(child, str):
                    value[key] = id_map.get(child, child)
                else:
                    remap(child)
        elif isinstance(value, list):
            for child in value:
                remap(child)
    remap(result)
    extend_boundaries(result, boundary_padding)
    for shape in result['MainPlatform']:
        extend_straight_join_handles(shape)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--merge-tolerance', type=float, default=1e-6)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.output.resolve() == args.level.resolve():
        parser.error('Export must not replace the authoring source; choose a separate output')
    if args.output.exists() and not args.overwrite:
        parser.error('Output exists; use --overwrite or choose another path')
    level = json.loads(args.level.read_text(encoding='utf-8'))
    exported = export_level(level, args.merge_tolerance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"MainPlatform: {len(level['MainPlatform'])} -> {len(exported['MainPlatform'])}; {args.output}")


if __name__ == '__main__':
    main()
