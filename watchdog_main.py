import argparse
from pathlib import Path

from bench_test.version import APP_VERSION
from bench_test.watchdog.state import SIMULATION_RECOVERY_ARG
from bench_test.watchdog.supervisor import run_supervisor


def main():
    parser = argparse.ArgumentParser(description="Bench Test Controller watchdog")
    parser.add_argument("--check-version", metavar="VERSION")
    parser.add_argument("--app-executable", type=Path)
    parser.add_argument("--app-version")
    parser.add_argument(SIMULATION_RECOVERY_ARG, action="store_true")
    args = parser.parse_args()
    if args.check_version is not None:
        if args.check_version != APP_VERSION:
            parser.error(f"Watchdog v{APP_VERSION} does not match BTC v{args.check_version}")
        return
    if args.app_version != APP_VERSION:
        parser.error(f"Watchdog v{APP_VERSION} requires --app-version {APP_VERSION}")
    if args.app_executable is None:
        parser.error("--app-executable is required")
    if not args.app_executable.is_absolute():
        parser.error("--app-executable must be an absolute path")
    run_supervisor(args.simulation_recovery, app_executable=str(args.app_executable))


if __name__ == "__main__":
    main()
