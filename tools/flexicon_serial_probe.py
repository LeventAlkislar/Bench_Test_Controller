"""Probe Flexicon 520Di/520DiN RS232 communication.

The default probe sends only read/status commands. It does not start, stop,
or change pump speed.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports


DEFAULT_PORT = "COM15"
DEFAULT_BAUD = 9600
DEFAULT_PUMP_ID = 1
READ_COMMANDS = ("ZY", "RS")


@dataclass(frozen=True)
class SerialConfig:
    baudrate: int
    stopbits: float
    line_ending: str

    @property
    def label(self) -> str:
        ending = self.line_ending.encode("unicode_escape").decode("ascii")
        return f"{self.baudrate} baud, stopbits={self.stopbits:g}, ending={ending}"


def list_ports() -> int:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return 1
    for port in ports:
        print(f"{port.device}\t{port.description}\t{port.hwid}")
    return 0


def read_response(ser: serial.Serial, timeout: float) -> bytes:
    deadline = time.time() + timeout
    data = bytearray()
    while time.time() < deadline:
        waiting = ser.in_waiting
        if waiting:
            data.extend(ser.read(waiting))
            if b"!" in data:
                break
        time.sleep(0.03)
    return bytes(data)


def printable(data: bytes) -> str:
    if not data:
        return "(no response)"
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r", "<CR>").replace("\n", "<LF>")


def open_serial(port: str, config: SerialConfig, timeout: float) -> serial.Serial:
    return serial.Serial(
        port=port,
        baudrate=config.baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=config.stopbits,
        timeout=timeout,
    )


def send_query(
    port: str,
    pump_id: int,
    command: str,
    config: SerialConfig,
    timeout: float,
) -> bytes:
    wire_command = f"{pump_id}{command.strip().upper()}"
    with open_serial(port, config, timeout) as ser:
        ser.reset_input_buffer()
        ser.write((wire_command + config.line_ending).encode("ascii"))
        return read_response(ser, timeout)


def probe(args: argparse.Namespace) -> int:
    commands = [cmd.strip().upper() for cmd in args.commands.split(",") if cmd.strip()]
    if not commands:
        commands = list(READ_COMMANDS)

    config = SerialConfig(args.baudrate, args.stopbits, args.line_ending)
    print(f"Port: {args.port}")
    print(f"Pump ID: {args.pump_id}")
    print(f"Config: {config.label}")

    got_response = False
    for command in commands:
        try:
            data = send_query(args.port, args.pump_id, command, config, args.timeout)
        except Exception as exc:
            print(f"{args.pump_id}{command} -> ERROR: {exc}")
            return 2
        got_response = got_response or bool(data)
        print(f"{args.pump_id}{command} -> {printable(data)}")
    return 0 if got_response else 1


def scan(args: argparse.Namespace) -> int:
    bauds = args.baudrates or [DEFAULT_BAUD, 19200, 4800, 2400, 1200]
    stopbits_values = [serial.STOPBITS_TWO, serial.STOPBITS_ONE]
    endings = ["\r", "\n", "\r\n"]
    ids = args.pump_ids or [args.pump_id]

    print(f"Scanning {args.port}; read-only command: ZY")
    got_response = False
    for pump_id in ids:
        for baudrate in bauds:
            for stopbits in stopbits_values:
                for ending in endings:
                    config = SerialConfig(baudrate, stopbits, ending)
                    try:
                        data = send_query(args.port, pump_id, "ZY", config, args.timeout)
                    except Exception as exc:
                        print(f"ID {pump_id}, {config.label} -> ERROR: {exc}")
                        return 2
                    if data:
                        got_response = True
                        print(f"ID {pump_id}, {config.label} -> {printable(data)}")
    if not got_response:
        print("No response on scanned read-only combinations.")
    return 0 if got_response else 1


def parse_csv_ints(text: str) -> list[int]:
    values = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            values.append(int(part))
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only RS232 probe for Flexicon 520Di/520DiN pumps."
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port, default: {DEFAULT_PORT}")
    parser.add_argument("--pump-id", type=int, default=DEFAULT_PUMP_ID, help="Pump ID/address")
    parser.add_argument("--timeout", type=float, default=2.0, help="Read timeout in seconds")

    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List serial ports")

    query = sub.add_parser("query", help="Send read-only status queries")
    query.add_argument("--baudrate", type=int, default=DEFAULT_BAUD)
    query.add_argument("--stopbits", type=float, default=serial.STOPBITS_TWO)
    query.add_argument("--line-ending", choices=["\r", "\n", "\r\n"], default="\r")
    query.add_argument(
        "--commands",
        default=",".join(READ_COMMANDS),
        help="Comma-separated read/status commands without pump ID. Default: ZY,RS",
    )

    scan_cmd = sub.add_parser("scan", help="Try read-only serial setting combinations")
    scan_cmd.add_argument(
        "--baudrates",
        type=parse_csv_ints,
        default=None,
        help="Comma-separated baudrates. Default: 9600,19200,4800,2400,1200",
    )
    scan_cmd.add_argument(
        "--pump-ids",
        type=parse_csv_ints,
        default=None,
        help="Comma-separated pump IDs to scan. Default: only --pump-id",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "list":
        return list_ports()
    if args.action == "query":
        return probe(args)
    if args.action == "scan":
        return scan(args)
    parser.error("unknown action")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
