import argparse
from pathlib import Path

from bench_test.watchdog.state import SIMULATION_RECOVERY_ARG
from bench_test.watchdog.supervisor import run_supervisor


def main():
    parser = argparse.ArgumentParser(description="Bench Test Controller watchdog")
    parser.add_argument("--app-executable", required=True, type=Path)
    parser.add_argument(SIMULATION_RECOVERY_ARG, action="store_true")
    args = parser.parse_args()
    if not args.app_executable.is_absolute():
        parser.error("--app-executable must be an absolute path")
    run_supervisor(args.simulation_recovery, app_executable=str(args.app_executable))


if __name__ == "__main__":
    main()
