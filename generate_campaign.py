"""Compatibility launcher for bike_stunt.campaign."""
from bike_stunt.campaign import *  # noqa: F401,F403
from bike_stunt.campaign import main
if __name__ == "__main__":
    raise SystemExit(main())
