"""Ground polygon contact and union, retaining exposed cubic Bezier edges.

Curves are flattened only for intersection discovery (1e-5 source units).
Uncut edges keep their original control points; cut edges use de Casteljau.
"""
import copy
import math
from functools import lru_cache

CURVE_ERROR = 1e-5
EPSILON = 1e-9


def xy(point):
    return point['x'], point['y']


def lerp(a, b, t):
    return tuple(x+(y-x)*t for x, y in zip(a, b))


def cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def subtract(a, b):
    return a[0]-b[0], a[1]-b[1]


def split(curve, t):
    a, b, c = [lerp(p, q, t) for p, q in zip(curve, curve[1:])]
    d, e = lerp(a, b, t), lerp(b, c, t)
    f = lerp(d, e, t)
    return (curve[0], a, d, f), (f, e, c, curve[-1])


def subcurve(curve, start, end):
    if start > end:
        return tuple(reversed(subcurve(curve, end, start)))
    if end < 1:
        curve = split(curve, end)[0]
    if start > 0:
        curve = split(curve, start/end)[1]
    return curve


def controls(a, b):
    def handle(p, name):
        h = p.get(name, {}) if p.get('tangentMode') != 'linear' else {}
        return p['x']+h.get('x', 0), p['y']+h.get('y', 0)
    return xy(a), handle(a, 'tangentOut'), handle(b, 'tangentIn'), xy(b)


def bounds(shape):
    values = [p for a, b in zip(shape['points'], shape['points'][1:]+shape['points'][:1])
              for p in controls(a, b)]
    return (min(p[0] for p in values), min(p[1] for p in values),
            max(p[0] for p in values), max(p[1] for p in values))


def bounds_touch(a, b, tolerance):
    return not (a[2]+tolerance < b[0] or b[2]+tolerance < a[0] or
                a[3]+tolerance < b[1] or b[3]+tolerance < a[1])


@lru_cache(maxsize=2048)
def flatten(curve):
    samples = [(0., curve[0])]
    def visit(part, start, end, depth):
        chord = subtract(part[-1], part[0])
        span = math.hypot(*chord)
        distance = max(abs(cross(subtract(p, part[0]), chord))/span
                       if span else math.dist(p, part[0]) for p in part[1:3])
        # Also split collinear reversals, which a chord alone cannot represent.
        projections = [sum(x*y for x, y in zip(subtract(p, part[0]), chord))
                       for p in part[1:3]]
        straight = all(-EPSILON <= v <= span*span+EPSILON for v in projections)
        if depth >= 20 or (distance <= CURVE_ERROR and straight):
            samples.append((end, part[-1]))
        else:
            left, right = split(part, .5)
            middle = (start+end)/2
            visit(left, start, middle, depth+1)
            visit(right, middle, end, depth+1)
    visit(curve, 0., 1., 0)
    return tuple(samples)


def segments(shape):
    result = []
    points = shape['points']
    for index, (a, b) in enumerate(zip(points, points[1:]+points[:1])):
        curve = controls(a, b)
        samples = flatten(curve)
        for (t0, p), (t1, q) in zip(samples, samples[1:]):
            if math.dist(p, q) > EPSILON:
                result.append((p, q, index, t0, t1, curve))
    return result


def on_segment(p, a, b, tolerance):
    delta = subtract(b, a)
    length = math.hypot(*delta)
    if length <= EPSILON:
        return math.dist(p, a) <= tolerance
    projection = sum(x*y for x, y in zip(subtract(p, a), delta))/(length*length)
    return (-tolerance/length <= projection <= 1+tolerance/length and
            abs(cross(subtract(p, a), delta)) <= tolerance*length)


def contains(p, edges, boundary=True, tolerance=EPSILON, box=None):
    if box is not None and not (box[0]-tolerance <= p[0] <= box[2]+tolerance and
                                box[1]-tolerance <= p[1] <= box[3]+tolerance):
        return False
    inside = False
    for a, b, *_ in edges:
        low_y, high_y = min(a[1], b[1]), max(a[1], b[1])
        if p[1] < low_y-tolerance or p[1] > high_y+tolerance:
            continue
        if (min(a[0], b[0])-tolerance <= p[0] <= max(a[0], b[0])+tolerance and
                on_segment(p, a, b, tolerance)):
            return boundary
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0]+(p[1]-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x > p[0]:
                inside = not inside
    return inside


def intersections(a, b, c, d, tolerance=EPSILON):
    if not bounds_touch((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])),
                        (min(c[0], d[0]), min(c[1], d[1]), max(c[0], d[0]), max(c[1], d[1])), tolerance):
        return []
    u, v, delta = subtract(b, a), subtract(d, c), subtract(c, a)
    denominator = cross(u, v)
    if abs(denominator) > EPSILON*math.hypot(*u)*math.hypot(*v):
        t, s = cross(delta, v)/denominator, cross(delta, u)/denominator
        if -EPSILON <= t <= 1+EPSILON and -EPSILON <= s <= 1+EPSILON:
            return [(max(0., min(1., t)), max(0., min(1., s)))]
        return []
    result = []
    for p, t in ((a, 0.), (b, 1.)):
        if on_segment(p, c, d, tolerance):
            s = sum(x*y for x, y in zip(subtract(p, c), v))/sum(x*x for x in v)
            result.append((t, max(0., min(1., s))))
    for p, s in ((c, 0.), (d, 1.)):
        if on_segment(p, a, b, tolerance):
            t = sum(x*y for x, y in zip(subtract(p, a), u))/sum(x*x for x in u)
            result.append((max(0., min(1., t)), s))
    return result


def touches(left, right, tolerance):
    if not bounds_touch(bounds(left), bounds(right), tolerance):
        return False
    a, b = segments(left), segments(right)
    if any(intersections(*a[i][:2], *b[j][:2], tolerance)
           for i, j in _edge_pairs(a, b, tolerance)):
        return True
    return contains(a[0][0], b) or contains(b[0][0], a)


def _edge_pairs(a, b, tolerance=EPSILON):
    """Sweep X bounds so distant tessellation segments never form pairs."""
    events = []
    for owner, group in enumerate((a, b)):
        for index, (p, q, *_) in enumerate(group):
            events.append((min(p[0], q[0]), owner, index, max(p[0], q[0]),
                           min(p[1], q[1]), max(p[1], q[1])))
    active = [[], []]
    for event in sorted(events):
        x, owner, index, _, low, high = event
        for group in (0, 1):
            active[group] = [e for e in active[group] if e[3]+tolerance >= x]
        for other in active[1-owner]:
            if high+tolerance >= other[4] and other[5]+tolerance >= low:
                yield (index, other[2]) if owner == 0 else (other[2], index)
        active[owner].append(event)


def _curve_parameter(edge, fraction):
    if fraction <= 0:
        return edge[3]
    if fraction >= 1:
        return edge[4]
    p, q, _, start, end, curve = edge
    direction = subtract(q, p)
    target = fraction*sum(v*v for v in direction)
    for _ in range(42):
        middle = (start+end)/2
        point = split(curve, middle)[0][-1]
        projection = sum(x*y for x, y in zip(subtract(point, p), direction))
        if projection < target:
            start = middle
        else:
            end = middle
    return (start+end)/2


def _outline(left, right):
    """Split crossing edges and retain the boundary of the filled union."""
    groups = [segments(left), segments(right)]
    boxes = [bounds(left), bounds(right)]
    cuts = [[{0., 1.} for _ in group] for group in groups]
    for i, j in _edge_pairs(*groups):
        for t, s in intersections(*groups[0][i][:2], *groups[1][j][:2]):
            cuts[0][i].add(t)
            cuts[1][j].add(s)
    retained = {}
    def key(p):
        return round(p[0], 8), round(p[1], 8)
    for owner, group in enumerate(groups):
        clockwise = sum(cross(e[0], e[1]) for e in group) < 0
        for edge, parameters in zip(group, cuts[owner]):
            a, b, index, t0, t1, curve = edge
            parameters = sorted(parameters)
            for start, end in zip(parameters, parameters[1:]):
                p, q = lerp(a, b, start), lerp(a, b, end)
                if math.dist(p, q) <= 1e-8:
                    continue
                middle = lerp(p, q, .5)
                if contains(middle, groups[1-owner], boundary=False, box=boxes[1-owner]):
                    continue
                if not clockwise:
                    p, q, start, end = q, p, end, start
                # Shared edges disappear only when filled on both sides.
                delta = subtract(q, p)
                length = math.hypot(*delta)
                outside = (middle[0]-delta[1]/length*1e-7,
                           middle[1]+delta[0]/length*1e-7)
                if contains(outside, groups[1-owner], boundary=False, box=boxes[1-owner]):
                    continue
                retained.setdefault((key(p), key(q)),
                    (p, q, (owner, index), _curve_parameter(edge, start),
                     _curve_parameter(edge, end), curve))
    edges = list(retained.values())
    outgoing = {}
    for i, edge in enumerate(edges):
        outgoing.setdefault(key(edge[0]), []).append(i)
    unused, contours = set(range(len(edges))), []
    while unused:
        first = min(unused)
        current, contour = first, []
        while current in unused:
            unused.remove(current)
            edge = edges[current]
            contour.append(edge)
            choices = [i for i in outgoing.get(key(edge[1]), []) if i in unused]
            if not choices:
                if key(edge[1]) != key(edges[first][0]):
                    raise ValueError('Ground union produced an open boundary')
                break
            direction = subtract(edge[1], edge[0])
            def turn(i):
                next_direction = subtract(edges[i][1], edges[i][0])
                return math.atan2(cross(direction, next_direction),
                                  sum(x*y for x, y in zip(direction, next_direction)))
            current = max(choices, key=turn)
        contours.append(contour)
    if len(contours) != 1:
        raise ValueError('Ground union requires one connected outline')
    return contours[0]


def union_shape(left, right):
    edges = _outline(left, right)
    # Begin at an original edge boundary so cyclic runs are coalesced as well.
    start = next((i for i, e in enumerate(edges) if e[2] != edges[i-1][2]), 0)
    edges = edges[start:]+edges[:start]
    runs = []
    for edge in edges:
        if runs and runs[-1][2] == edge[2] and abs(runs[-1][4]-edge[3]) < 1e-8:
            previous = runs[-1]
            runs[-1] = (previous[0], edge[1], edge[2], previous[3], edge[4], edge[5])
        else:
            runs.append(edge)
    points = []
    curves = []
    for p, q, _, start, end, curve in runs:
        clipped = subcurve(curve, start, end)
        curves.append((p, clipped[1], clipped[2], q))
    for i, curve in enumerate(curves):
        x, y = curve[0]
        incoming, outgoing = subtract(curves[i-1][2], (x, y)), subtract(curve[1], (x, y))
        _, _, (owner, index), start, _, _ = runs[i]
        original = (left, right)[owner]['points'][index] if start == 0 else {}
        in_length, out_length = math.hypot(*incoming), math.hypot(*outgoing)
        smooth = (not original.get('corner') and in_length > EPSILON and out_length > EPSILON and
                  abs(cross(incoming, outgoing)) <= 1e-8*in_length*out_length and
                  sum(a*b for a, b in zip(incoming, outgoing)) < 0)
        points.append({'x': x, 'y': y,
                       'tangentIn': dict(zip(('x', 'y'), incoming)),
                       'tangentOut': dict(zip(('x', 'y'), outgoing)),
                       'tangentMode': 'continuous' if smooth else 'broken',
                       'corner': original.get('corner', False)})
    # Clockwise outlines traverse the top from left to right.
    first = min(range(len(points)), key=lambda i: (points[i]['x'], -points[i]['y']))
    points = points[first:]+points[:first]
    last = max(range(len(points)), key=lambda i: (points[i]['x'], points[i]['y']))
    result = copy.deepcopy(left)
    result['points'] = points
    result.setdefault('metadata', {}).update(drivingSurfaceCount=last+1,
                                            terrainUnion=True, freeBottomCorners=True)
    return result
