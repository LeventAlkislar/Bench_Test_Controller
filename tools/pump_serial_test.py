"""Small CLI for testing the Arduino Nano pump controller.

This script talks to PUMP_CTRL_V1/V2 firmware over a serial COM port.
It is intentionally standalone so pump tests can be done before touching
the main Bench Test Controller UI.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports


BAUD_RATE = 115200
DEFAULT_TIMEOUT = 2.0
EXPECTED_IDS = {"ID:PUMP_CTRL_V2", "ID:PUMP_CTRL_V1"}


@dataclass
class PumpSerial:
    port: str
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        self.ser: serial.Serial | None = None

    def __enter__(self) -> "PumpSerial":
        self.ser = serial.Serial(self.port, BAUD_RATE, timeout=self.timeout)
        time.sleep(2.0)  # Nano resets when the serial port opens.
        self._drain_boot_messages()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _drain_boot_messages(self) -> None:
        assert self.ser is not None
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if not self.ser.in_waiting:
                time.sleep(0.05)
                continue
            self.ser.readline()

    def send(self, command: str, wait: float = 0.25) -> list[str]:
        assert self.ser is not None
        command = command.strip()
        if not command:
            return []

        self.ser.reset_input_buffer()
        self.ser.write((command + "\n").encode("ascii"))
        time.sleep(wait)
        return self.read_available()

    def read_available(self) -> list[str]:
        assert self.ser is not None
        lines: list[str] = []
        while self.ser.in_waiting:
            raw = self.ser.readline()
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)
        return lines

    def require_identity(self) -> None:
        lines = self.send("ID?", wait=0.3)
        if not any(line in EXPECTED_IDS for line in lines):
            got = " | ".join(lines) if lines else "no response"
            raise RuntimeError(f"unexpected device identity: {got}")


def list_ports() -> int:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return 1

    for port in ports:
        print(f"{port.device}\t{port.description}\t{port.hwid}")
    return 0


def print_lines(lines: list[str]) -> None:
    if lines:
        for line in lines:
            print(line)
    else:
        print("(no response)")


def with_pump(args: argparse.Namespace, command: str, wait: float = 0.25) -> int:
    try:
        with PumpSerial(args.port, timeout=args.timeout) as pump:
            if not args.skip_id:
                pump.require_identity()
            print_lines(pump.send(command, wait=wait))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def interactive(args: argparse.Namespace) -> int:
    try:
        with PumpSerial(args.port, timeout=args.timeout) as pump:
            if not args.skip_id:
                pump.require_identity()

            print("Connected. Commands: STATUS, STOP, RUN,FWD, RUN,REV,")
            print("PULSE,FWD,<ms>, PULSE,REV,<ms>, SPEEDV,<mV>, SPEED,<rpm>, MAXRPM,<rpm>")
            print("Type quit to exit.")

            while True:
                try:
                    command = input("pump> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if command.lower() in {"q", "quit", "exit"}:
                    break
                print_lines(pump.send(command, wait=0.3))

        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test the Arduino pump controller.")
    parser.add_argument("--port", default="COM14", help="Serial port, default: COM14")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Serial timeout in seconds")
    parser.add_argument("--skip-id", action="store_true", help="Do not require a known pump controller ID before sending")

    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="List serial ports")
    sub.add_parser("interactive", help="Open an interactive command prompt")
    sub.add_parser("id", help="Read device identity")
    sub.add_parser("status", help="Read controller status")
    sub.add_parser("stop", help="Stop pump")

    run = sub.add_parser("run", help="Run continuously in a direction")
    run.add_argument("direction", choices=["FWD", "REV", "fwd", "rev"])

    pulse = sub.add_parser("pulse", help="Run for a fixed duration")
    pulse.add_argument("direction", choices=["FWD", "REV", "fwd", "rev"])
    pulse.add_argument("time_ms", type=int)

    speedv = sub.add_parser("speedv", help="Set DAC output in millivolts")
    speedv.add_argument("millivolts", type=int)

    speed = sub.add_parser("speed", help="Set pump speed in rpm using firmware max rpm")
    speed.add_argument("rpm", type=float)

    maxrpm = sub.add_parser("maxrpm", help="Set firmware max rpm")
    maxrpm.add_argument("rpm", type=float)

    raw = sub.add_parser("raw", help="Send a raw firmware command")
    raw.add_argument("command")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.action == "list":
        return list_ports()
    if args.action == "interactive":
        return interactive(args)
    if args.action == "id":
        return with_pump(args, "ID?")
    if args.action == "status":
        return with_pump(args, "STATUS")
    if args.action == "stop":
        return with_pump(args, "STOP")
    if args.action == "run":
        return with_pump(args, f"RUN,{args.direction.upper()}")
    if args.action == "pulse":
        return with_pump(args, f"PULSE,{args.direction.upper()},{args.time_ms}", wait=0.5)
    if args.action == "speedv":
        return with_pump(args, f"SPEEDV,{args.millivolts}")
    if args.action == "speed":
        return with_pump(args, f"SPEED,{args.rpm}")
    if args.action == "maxrpm":
        return with_pump(args, f"MAXRPM,{args.rpm}")
    if args.action == "raw":
        return with_pump(args, args.command)

    parser.error("unknown action")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
