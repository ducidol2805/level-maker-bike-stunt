"""Shared world-space shape labels for native and exported map previews."""


def shape_labels(shape):
    stored = shape.get('metadata', {}).get('sourceShapeLabels')
    if stored:
        return stored
    points = shape.get('points', [])
    if not points or not shape.get('id'):
        return []
    xs, ys = [p['x'] for p in points], [p['y'] for p in points]
    return [{'id': shape['id'], 'x': (min(xs) + max(xs)) / 2,
             'y': (min(ys) + max(ys)) / 2}]


def map_shape_labels(data):
    for group in ('MainPlatform', 'RampPlatform', 'FreePlatform', 'deadzone', 'InteractableObject'):
        for shape in data.get(group, []):
            if 'points' in shape:
                yield from shape_labels(shape)
