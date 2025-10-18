import argparse
import curses

def get_parser():
    parser = argparse.ArgumentParser(description="BENCHLAB Telemetry")
    parser.add_argument("-tui", action="store_true", help="enable TUI (default)")
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        help="TUI or logging refresh interval in seconds")
    parser.add_argument("-logfleet", "--logfleet", action="store_true",
                        help="Run without TUI, log any or all devices")
    parser.add_argument("-mqtt", nargs="?", const="localhost",
                        help="MQTT publisher to localhost mosquitto")
    parser.add_argument("-graph", action="store_true",
                        help="Launch GUI graphing mode")
    parser.add_argument("-vu", action="store_true",
                        help="Launch VU analog dials")
    parser.add_argument("-vuconfig", action="store_true",
                        help="Launch VU configuration interface")
    parser.add_argument("-wigidash", action="store_true",
                    help="Connect to WigiDash")
    return parser

def launch_mode():
    parser = get_parser()
    args = parser.parse_args()

    if args.logfleet:
        try:
            from benchlab.csv_log.csv_logger import run_csv_logger
            run_csv_logger(args.interval)
        except ModuleNotFoundError:
            print("CSV logger not available in this build.")
            return

    elif args.mqtt:
        try:
            from benchlab.mqtt.mqtt_publisher import run_mqtt_mode
            broker = args.mqtt if args.mqtt else "localhost"
            run_mqtt_mode(broker)
        except ModuleNotFoundError:
            print("MQTT module not available in this build.")
            return

    elif args.graph:
        try:
            from benchlab.graph.runner import run_graph_mode
            run_graph_mode()
        except ModuleNotFoundError:
            print("Graph module not available in this build.")
            return

    elif args.vu:
        try:
            from benchlab.vu.vu_updater import run_updater
            run_updater()
        except ModuleNotFoundError:
            print("VU module not available in this build.")
            return

    elif args.vuconfig:
        try:
            from benchlab.vu.vu_tui import launch_vu_config
            launch_vu_config()
        except ModuleNotFoundError:
            print("VU configuration not available in this build.")
            return

    elif args.wigidash:
        try:
            from benchlab.wigidash.run_wigi import run_wigidash
            run_wigidash()
        except ModuleNotFoundError:
            print("WigiDash module not available in this build.")

    else:  # default: TUI
        try:
            from benchlab.tui.tui_main import tui_main
            curses.wrapper(tui_main, None, args)
        except ModuleNotFoundError:
            print("TUI module not available in this build.")
            return

def main():
    launch_mode()

if __name__ == "__main__":
    main()
