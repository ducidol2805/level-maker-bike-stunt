"""Compatibility exports for bike_stunt.obstacle_library."""
from bike_stunt.obstacle_library import *  # noqa: F401,F403
from bike_stunt.obstacle_library import main
from bike_stunt.library_variants import build_variant as _build_variant

def build_variant(family, variant, *, scale=1):
    return _build_variant(family, variant, scale=scale, deadzone_y_offset=DEADZONE_Y_OFFSET)

if __name__ == "__main__":
    raise SystemExit(main())
