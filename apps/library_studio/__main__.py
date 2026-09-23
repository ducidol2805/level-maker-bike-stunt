import argparse
import tkinter as tk
from bike_stunt.library_studio.window import ObstacleLibraryWindow

def main():
    parser = argparse.ArgumentParser(description="Open the obstacle Library Studio.")
    parser.parse_args()
    root = tk.Tk()
    root.withdraw()
    studio = ObstacleLibraryWindow(root)
    studio.window.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
