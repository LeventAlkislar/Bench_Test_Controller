import threading
import time
from typing import Optional

import serial


FLEXICON_DEFAULT_BAUD_RATE = 9600


class FlexiconPumpController:
    """Serial controller for Flexicon 520Di/520DiN network mode."""

    def __init__(self, pump_id: int = 1, baudrate: int = FLEXICON_DEFAULT_BAUD_RATE):
        self.serial_port: Optional[serial.Serial] = None
        self.port: str = ""
        self.pump_id = pump_id
        self.baudrate = baudrate
        self.lock = threading.Lock()

    def configure(self, pump_id: int = None, baudrate: int = None):
        if pump_id is not None:
            self.pump_id = max(1, min(64, int(pump_id)))
        if baudrate is not None:
            self.baudrate = int(baudrate)

    def connect(self, port: str, timeout: float = 2.0) -> bool:
        try:
            ser = serial.Serial(
                port=port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                timeout=timeout,
            )
            self.serial_port = ser
            self.port = port
            self._drain_input()

            lines = self.status()
            if not lines or any(line.startswith("ERR") for line in lines):
                self.disconnect(stop_first=False)
                return False
            return True
        except serial.SerialException:
            self.serial_port = None
            self.port = ""
            return False

    def disconnect(self, stop_first: bool = True):
        if stop_first and self.is_connected():
            try:
                self.stop()
            except Exception:
                pass
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self.port = ""

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command: str, wait: float = 0.3) -> list[str]:
        if not self.is_connected():
            return ["ERR NOT_CONNECTED"]

        command = command.strip()
        if not command:
            return []

        if command[:1].isdigit() or command[:1] == "#":
            wire_command = command
        else:
            wire_command = f"{self.pump_id}{command}"

        with self.lock:
            self.serial_port.write((wire_command + "\r").encode("ascii"))
            return self._read_response_locked(wire_command, wait)

    def read_available(self) -> list[str]:
        if not self.is_connected():
            return []
        with self.lock:
            return self._read_response_locked("", 0.05)

    def _read_response_locked(self, echo_command: str, wait: float) -> list[str]:
        deadline = time.time() + max(0.01, wait)
        data = bytearray()
        while time.time() < deadline:
            if not self.serial_port or not self.serial_port.in_waiting:
                time.sleep(0.02)
                continue
            data.extend(self.serial_port.read(self.serial_port.in_waiting))
            if b"!" in data:
                break

        text = data.decode("utf-8", errors="replace")
        parts = []
        for chunk in text.replace("\r", "\n").split("\n"):
            chunk = " ".join(chunk.strip().split())
            if not chunk or chunk == echo_command:
                continue
            parts.append(chunk)
        return parts

    def _drain_input(self):
        if not self.is_connected():
            return
        deadline = time.time() + 0.3
        while time.time() < deadline:
            if not self.serial_port.in_waiting:
                time.sleep(0.03)
                continue
            self.serial_port.read(self.serial_port.in_waiting)

    def status(self) -> list[str]:
        lines = self.send_command("RS", wait=0.5)
        return [f"STATUS {line}" for line in lines] if lines else ["ERR NO_RESPONSE"]

    def running_status(self) -> list[str]:
        lines = self.send_command("ZY", wait=0.3)
        return [f"STATUS RUNNING={line}" for line in lines] if lines else ["ERR NO_RESPONSE"]

    def stop(self) -> list[str]:
        lines = self.send_command("ST", wait=0.2)
        return lines or ["OK STOP"]

    def run(self, direction: str = "FWD") -> list[str]:
        self.set_direction(direction)
        lines = self.send_command("GO", wait=0.2)
        return lines or ["OK RUN FWD"]

    def pulse(self, direction: str, duration_ms: int) -> list[str]:
        raise NotImplementedError("Flexicon pump does not support time-based pulse mode")

    def set_direction(self, direction: str) -> list[str]:
        direction = self._normalize_direction(direction)
        command = "RR" if direction == "FWD" else "RL"
        lines = self.send_command(command, wait=0.2)
        return lines or [f"OK DIR {direction}"]

    def set_speed_mv(self, millivolts: int) -> list[str]:
        raise NotImplementedError("Flexicon pump speed is controlled in RPM, not mV")

    def set_speed_rpm(self, rpm: float) -> list[str]:
        rpm = max(0.0, float(rpm))
        lines = self.send_command(f"SP {rpm:.1f}", wait=0.2)
        return lines or [f"OK SPEED {rpm:.1f} RPM"]

    def set_max_rpm(self, rpm: float) -> list[str]:
        return [f"OK MAXRPM {float(rpm):.1f} (software setting only)"]

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        direction = (direction or "").strip().upper()
        if direction in {"FWD", "FORWARD"}:
            return "FWD"
        if direction in {"REV", "REVERSE"}:
            return "REV"
        raise ValueError("Pump direction must be FWD or REV")
