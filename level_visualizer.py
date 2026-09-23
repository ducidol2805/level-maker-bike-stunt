"""Compatibility launcher for the map viewer and legacy imports."""
from bike_stunt.map_viewer.validation import *  # noqa: F401,F403
from bike_stunt.map_viewer.canvas import *  # noqa: F401,F403
from bike_stunt.library_studio.window import ObstacleCard, ObstacleGroup, compile_obstacle_card, ObstacleEditorWindow, ObstacleLibraryWindow  # noqa: F401
from bike_stunt.map_viewer.browser import LevelBrowserApp, ttk_frame, launch_browser, main

if __name__ == "__main__":
    raise SystemExit(main())
