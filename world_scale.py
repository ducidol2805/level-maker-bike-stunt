"""Scale authored world coordinates while preserving dimensionless metadata."""
from __future__ import annotations

import math


SPATIAL_SCALARS = {
    'minX', 'maxX', 'minY', 'maxY', 'triggerX', 'recenterX',
    'entryY', 'exitY', 'lookAheadDistance', 'boundaryPadding',
    'length', 'landingLength', 'fitExitY', 'baselineLength',
    'targetLength', 'extraFinishLength', 'clearRunout',
    'checkpointCandidateAfterStabilization', 'scale',
}
SPATIAL_ARRAYS = {
    'groundSamples', 'rewardLine', 'surface', 'xRange', 'recoveryRange',
    'approach', 'launch', 'flight', 'landing', 'recovery',
    'brokenPlatformGaps',
}
SPEED_SCALARS = {
    'speed', 'airtime', 'flightTimeSeconds', 'expectedRestartSpeed',
    'barrelEntrySpeed', 'barrelImpulseY',
}


def scale_world_data(data, factor):
    """Copy geometry and derived metadata into a new world-unit scale.

    Gravity stays fixed, so ballistic speeds and times use sqrt(factor).
    Counts, angles, ratios, directions and event delays remain unchanged.
    """
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('World scale must be positive and finite')
    motion_factor = math.sqrt(factor)

    def numeric_factor(path):
        name = path[-1]
        parents = path[:-1]
        if name in ('x', 'y'):
            return motion_factor if any(key in parents for key in ('force', 'assumedEntryVelocity')) else factor
        if name == '[]' and any(key in parents for key in SPATIAL_ARRAYS):
            return factor
        if name in SPATIAL_SCALARS:
            return factor
        if name in SPEED_SCALARS:
            return motion_factor
        if name in ('minimum', 'ideal', 'maximum') and 'entrySpeedWindow' in parents:
            return motion_factor
        if name == 'minimum' and 'entrySpeedEstimate' in parents:
            return motion_factor
        if name == 'value' and 'loopSpeedEstimate' in parents:
            return motion_factor
        return 1

    def visit(value, path=()):
        if isinstance(value, dict):
            return {key: visit(item, path+(key,)) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, path+('[]',)) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item, path+('[]',)) for item in value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value*numeric_factor(path)
        return value

    return visit(data)
