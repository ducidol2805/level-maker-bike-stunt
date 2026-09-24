"""Remove tiny generated union edges and reject unsafe exported spline knots."""
import math

# Source-unit tolerances; use the same thresholds on an already scaled export.
UNION_POINT_MERGE_DISTANCE = .05
MIN_SPLINE_POINT_DISTANCE = .01
SHAPE_GROUPS = ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone', 'InteractableObject')


def distance(a, b):
    return math.hypot(a['x']-b['x'], a['y']-b['y'])


def collapse_union_edges(shape, tolerance=UNION_POINT_MERGE_DISTANCE):
    """Collapse adjacent union artifacts without rounding other authored knots."""
    if not shape.get('metadata', {}).get('terrainUnion'):
        return
    points = shape['points']
    count = shape['metadata']['drivingSurfaceCount']
    while True:
        pair = next(((i, (i+1) % len(points)) for i in range(len(points))
                     if distance(points[i], points[(i+1) % len(points)]) < tolerance), None)
        if pair is None:
            break
        if len(points) <= 3:
            raise ValueError(f"{shape['id']}: ground union is too small for a valid spline")
        first, second = pair
        # Preserve the first road knot across the closing seam.
        keep, remove = (second, first) if second == 0 else (first, second)
        retained, discarded = points[keep], points[remove]
        key = 'tangentIn' if second == 0 else 'tangentOut'
        handle = discarded.get(key, {}) if discarded.get('tangentMode') != 'linear' else {}
        vector = {axis: handle.get(axis, 0) for axis in ('x', 'y')}
        if any(vector.values()):
            vector = {axis: discarded[axis]+vector[axis]-retained[axis] for axis in ('x', 'y')}
        if retained.get('tangentMode') == 'linear':
            retained['tangentIn'] = {'x': 0, 'y': 0}
            retained['tangentOut'] = {'x': 0, 'y': 0}
        retained[key] = vector
        retained['tangentMode'] = 'broken'
        retained['corner'] = retained.get('corner', False) or discarded.get('corner', False)
        if remove < count:
            count -= 1
        del points[remove]
    shape['metadata']['drivingSurfaceCount'] = count


def validate_spline_spacing(level):
    """Fail with the shape and indices rather than writing an unusable export."""
    minimum = MIN_SPLINE_POINT_DISTANCE*level.get('map', {}).get('worldScale', 1)
    for group in SHAPE_GROUPS:
        for shape in level.get(group, []):
            points = shape.get('points', [])
            pairs = [(i, i+1) for i in range(len(points)-1)]
            if shape.get('closed') and len(points) > 1:
                pairs.append((len(points)-1, 0))
            for i, j in pairs:
                gap = distance(points[i], points[j])
                if gap < minimum:
                    raise ValueError(f"{group}/{shape['id']}: points {i}/{j} too close "
                                     f"({gap:.9g} < {minimum:.9g})")
