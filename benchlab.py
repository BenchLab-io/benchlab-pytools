#!/usr/bin/env python3

import sys, os, subprocess
from benchlab.main import get_parser, launch_mode, main

MODES = {
    "TUI":       {"flag": "-tui",      "reqs": ["tui"],      "desc": "Interactive terminal UI", 
                  "info": "Displays a live TUI for monitoring connected devices and telemetry. Supports multiple devices."},
    "LogFleet":  {"flag": "-logfleet", "reqs": ["csv_log"],  "desc": "CSV logging", 
                  "info": "Logs data from one or multiple devices into CSV files for offline analysis. Supports single device and fleet logging."},
    "MQTT":      {"flag": "-mqtt",     "reqs": ["mqtt"],     "desc": "MQTT publisher", 
                  "info": "Publishes telemetry data to an MQTT broker, allowing integration with external dashboards."},
    "Graph":     {"flag": "-graph",    "reqs": ["graph"],    "desc": "GUI graphing", 
                  "info": "Launches a graphical interface to visualize telemetry trends in real time."},
    "VU":        {"flag": "-vu",       "reqs": ["vu"],       "desc": "VU analog dials", 
                  "info": "Displays analog-style VU dials for visual monitoring of device metrics."},
    "VU Config": {"flag": "-vuconfig", "reqs": ["vu"],       "desc": "VU configuration UI", 
                  "info": "Interactive configuration interface for customizing VU dials and settings."},
}

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def show_info():
    clear_screen()
    print("=== BENCHLAB Link Info ===\n")
    for i, m in enumerate(MODES.keys(), 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
        print(f"   {MODES[m]['info']}\n")
    input("Press Enter to return to menu or exit...")
    sys.exit(0)

def install_requirements(mods):
    """Try to install requirements_<name>.txt for each mod, skip silently if missing."""
    for m in mods:
        for tag in MODES[m]["reqs"]:
            req_file = f"requirements_{tag}.txt"
            if os.path.exists(req_file):
                print(f"[INFO] Installing requirements for {m} ({req_file})...")
                subprocess.call([sys.executable, "-m", "pip", "install", "-r", req_file])

def interactive_menu():
    clear_screen()
    print("=== BENCHLAB Link Launcher ===\n")
    for i, m in enumerate(MODES.keys(), 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
    print("\nSelect features to enable (e.g. 1,3,5 or 'all') or type 'info' for info:")
    choice = input("> ").strip()

    if choice.lower() == "info":
        show_info()
    elif choice.lower() == "all":
        selected = list(MODES.keys())
    else:
        selected = []
        for c in choice.split(","):
            try:
                idx = int(c.strip()) - 1
                selected.append(list(MODES.keys())[idx])
            except (ValueError, IndexError):
                pass

    if not selected:
        print("No valid selection, exiting.")
        sys.exit(0)

    install_requirements(selected)

    # Ask which function to start
    clear_screen()
    print("=== BENCHLAB Link Launcher ===\n")
    for i, m in enumerate(selected, 1):
        print(f"{i}. {m} - {MODES[m]['desc']}")
    print("\nWhich function do you want to start?")
    c = input("> ").strip()
    try:
        start = selected[int(c) - 1]
    except (ValueError, IndexError):
        print("Invalid choice, exiting.")
        sys.exit(1)

    # Rewrite sys.argv and dispatch to normal main logic
    flag = MODES[start]["flag"]
    sys.argv = [sys.argv[0], flag]
    launch_mode()


if __name__ == "__main__":
    try:
        if len(sys.argv) == 1:
            interactive_menu()
        elif sys.argv[1].lower() in ("-info", "--info"):
            show_info()
        else:
            main()
    except Exception as e:
        print(f"[BENCHLAB LINK ERROR] {e}", file=sys.stderr)
        sys.exit(1)