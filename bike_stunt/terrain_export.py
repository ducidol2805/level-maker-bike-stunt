"""Merge touching ground while retaining exposed Bezier terrain curves."""
import argparse
import copy
import json
import math
from pathlib import Path


def surface_count(shape):
    points = shape['points']
    metadata = shape.get('metadata', {})
    count = metadata.get('drivingSurfaceCount', len(points)-2)
    merged_corners = metadata.get('mergedClosePoints', False)
    expected_bottom_count = len(points)-count
    union = metadata.get('terrainUnion', False)
    if (not shape.get('closed') or count < 2 or count > len(points) or
            (not union and expected_bottom_count != 2 and not (merged_corners and expected_bottom_count in (0, 1)))):
        raise ValueError(f"{shape.get('id')}: expected a surface followed by bottom corners")
    if expected_bottom_count == 2:
        right, left = points[-2:]
        if not metadata.get('freeBottomCorners') and (abs(right['x']-points[count-1]['x']) > 1e-6 or
                abs(left['x']-points[0]['x']) > 1e-6 or
                max(right['y'], left['y']) >= min(p['y'] for p in points[:count])):
            raise ValueError(f"{shape.get('id')}: unsupported terrain closure; cannot safely infer bottom corners")
    if not union and any(a['x'] > b['x'] for a, b in zip(points[:count], points[1:count])):
        raise ValueError(f"{shape.get('id')}: driving surface must run left to right")
    return count


def normalize_main_platform(level):
    """Copy and validate terrain without moving its authored bottom corners."""
    result = copy.deepcopy(level)
    shapes = result.get('MainPlatform', [])
    for shape in shapes:
        surface_count(shape)
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
        shape['points'] = surface + shape['points'][n:]
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


def can_merge_terrain(left, right, tolerance=1e-6):
    """Any filled ground contact qualifies, including overlap and containment."""
    from bike_stunt.terrain_union import touches
    n = surface_count(left)
    surface_count(right)
    a, b = left['points'][n-1], right['points'][0]
    if math.hypot(a['x']-b['x'], a['y']-b['y']) <= tolerance:
        return True
    return touches(left, right, tolerance)


def _can_splice(left, right, tolerance):
    """Keep exact road knots for ordinary endpoint joins with a safe underside."""
    from bike_stunt.terrain_union import controls
    n, m = surface_count(left), surface_count(right)
    a, b = left['points'][n-1], right['points'][0]
    if math.hypot(a['x']-b['x'], a['y']-b['y']) > tolerance:
        return False
    for shape, count in ((left, n), (right, m)):
        points = shape['points']
        if len(points)-count != 2:
            return False
        bottom_right, bottom_left = points[-2:]
        if (abs(bottom_right['x']-points[count-1]['x']) > tolerance or
                abs(bottom_left['x']-points[0]['x']) > tolerance):
            return False
        handles = ((points[0], 'tangentIn'), (points[count-1], 'tangentOut'))
        handles += tuple((p, key) for p in points[-2:] for key in ('tangentIn', 'tangentOut'))
        if any(p.get('tangentMode') != 'linear' and
               math.hypot(p.get(key, {}).get('x', 0), p.get(key, {}).get('y', 0)) > tolerance
               for p, key in handles):
            return False
    low_left, low_right = left['points'][-1], right['points'][-2]
    width = low_right['x']-low_left['x']
    if width <= tolerance:
        return False
    slope = (low_right['y']-low_left['y'])/width
    # Compare the bottom to the road at the same X, not a global minimum Y.
    return all(y-low_left['y']-slope*(x-low_left['x']) > tolerance
               for shape, count in ((left, n), (right, m))
               for p, q in zip(shape['points'][:count], shape['points'][1:count])
               for x, y in controls(p, q))


def _merge_ground(left, right, tolerance):
    from bike_stunt.shape_labels import shape_labels
    from bike_stunt.terrain_union import union_shape
    source_labels = copy.deepcopy(shape_labels(left)+shape_labels(right))
    sources = list(dict.fromkeys(left.get('metadata', {}).get('sourceShapeIds', [left['id']])+
                                 right.get('metadata', {}).get('sourceShapeIds', [right['id']])))
    if _can_splice(left, right, tolerance):
        n, m = surface_count(left), surface_count(right)
        a, b = left['points'][n-1], right['points'][0]
        incoming = a.get('tangentIn', {}) if a.get('tangentMode') != 'linear' else {}
        outgoing = b.get('tangentOut', {}) if b.get('tangentMode') != 'linear' else {}
        mode = ('continuous' if not a.get('corner') and not b.get('corner') and
                are_opposite_tangents(incoming, outgoing) else 'broken')
        a.update(tangentIn={'x': incoming.get('x', 0), 'y': incoming.get('y', 0)},
                 tangentOut={'x': outgoing.get('x', 0), 'y': outgoing.get('y', 0)},
                 tangentMode=mode)
        left['points'] = left['points'][:n]+right['points'][1:m]+[right['points'][-2], left['points'][-1]]
        result = left
        meta = result.setdefault('metadata', {})
        meta['drivingSurfaceCount'] = n+m-1
        if (right.get('metadata', {}).get('freeBottomCorners') or
                max(p['y'] for p in result['points'][-2:]) >=
                min(p['y'] for p in result['points'][:n+m-1])):
            meta['freeBottomCorners'] = True
    else:
        result = union_shape(left, right)
    result.setdefault('metadata', {}).update(sourceShapeLabels=source_labels, sourceShapeIds=sources)
    return result


def export_level(level, tolerance=1e-6, *, boundary_padding=None):
    """Merge every connected ground component, never bridge real gaps.

    Preserve incoming/outgoing Bezier handles at each shared road vertex.
    Keep the first shape ID and remap fallback references to surviving IDs.
    """
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError('tolerance must be finite and non-negative')
    if boundary_padding is None:
        boundary_padding = level.get('terrainExport', {}).get('boundaryPadding', 20)
    result = normalize_main_platform(level)
    shapes = sorted(result.get('MainPlatform', []), key=lambda s: s['points'][0]['x'])
    merged, id_map = shapes, {}
    changed = True
    while changed:
        changed = False
        for i, left in enumerate(merged):
            for j in range(i+1, len(merged)):
                right = merged[j]
                if can_merge_terrain(left, right, tolerance):
                    merged[i] = _merge_ground(left, right, tolerance)
                    merged.pop(j)
                    changed = True
                    break
            if changed:
                break
    merged.sort(key=lambda shape: shape['points'][0]['x'])
    for shape in merged:
        for sid in [shape['id'], *shape.get('metadata', {}).get('sourceShapeIds', [])]:
            id_map[sid] = shape['id']
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
    from bike_stunt.spline_spacing import (collapse_union_edges, validate_spline_spacing,
                                          UNION_POINT_MERGE_DISTANCE)
    spacing = UNION_POINT_MERGE_DISTANCE*result.get('map', {}).get('worldScale', 1)
    for shape in result['MainPlatform']:
        collapse_union_edges(shape, spacing)
        extend_straight_join_handles(shape)
    validate_spline_spacing(result)
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
