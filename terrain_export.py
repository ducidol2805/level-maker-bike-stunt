"""Compatibility exports for bike_stunt.terrain_export."""
from bike_stunt.terrain_export import *  # noqa: F401,F403
if __name__ == "__main__":
    from bike_stunt.terrain_export import main
    raise SystemExit(main())
