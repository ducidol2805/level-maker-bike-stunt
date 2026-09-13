"""SpringObject authoring contract and idealized cinematic trajectory preview.

Unity must implement trigger entry, velocity assignment and rearming. This
module is not a vehicle simulation and does not apply a force to a real player.
"""
import math


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return value


def spring_parameters(item):
    try:
        transform, props = item['transform'], item['properties']
        target = props['targetPosition']
        x, y = (_number(transform[k], 'SpringObject.transform.'+k) for k in ('x', 'y'))
        tx, ty = (_number(target[k], 'SpringObject.targetPosition.'+k) for k in ('x', 'y'))
        duration = _number(props['flightTimeSeconds'], 'SpringObject.flightTimeSeconds')
        gravity = _number(props.get('gravity', 9.81), 'SpringObject.gravity')
    except (KeyError, TypeError) as exc:
        raise ValueError('SpringObject needs transform, targetPosition and flightTimeSeconds') from exc
    if duration <= 0 or gravity <= 0:
        raise ValueError('SpringObject flightTimeSeconds and gravity must be positive')
    if ty <= y:
        raise ValueError('SpringObject targetPosition must be higher than its trigger')
    for key, expected in (('launchMode', 'target_arc'), ('activation', 'on_player_enter'),
                          ('rearm', 'on_exit')):
        if props.get(key, expected) != expected:
            raise ValueError(f'SpringObject {key} must be {expected}')
    if 'cinematic' in props and not isinstance(props['cinematic'], bool):
        raise ValueError('SpringObject cinematic must be a boolean')
    return x, y, tx, ty, duration, gravity


def spring_trajectory(item, steps=48):
    """Target arc, assuming zero drag and replacement of incoming velocity.

    targetPosition is the bike reference-point position, not ground contact.
    Include the exact apex so camera bounds cannot clip the preview arc.
    """
    x, y, tx, ty, duration, gravity = spring_parameters(item)
    vx = (tx-x)/duration
    vy = (ty-y)/duration + .5*gravity*duration
    times = {duration*i/steps for i in range(steps+1)}
    times.add(max(0, min(duration, vy/gravity)))
    return [(x+vx*t, y+vy*t-.5*gravity*t*t) for t in sorted(times)]
