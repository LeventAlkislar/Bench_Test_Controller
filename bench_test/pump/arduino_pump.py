import threading
import time
from typing import Optional

import serial


PUMP_BAUD_RATE = 115200
PUMP_DEVICE_IDS = {"ID:PUMP_CTRL_V2", "ID:PUMP_CTRL_V1"}


class ArduinoPumpController:
    """Serial controller for the Arduino Nano peristaltic pump firmware."""

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.port: str = ""
        self.lock = threading.Lock()

    def connect(self, port: str, timeout: float = 2.0) -> bool:
        try:
            ser = serial.Serial(port=port, baudrate=PUMP_BAUD_RATE, timeout=timeout)
            time.sleep(2.0)  # Nano resets when the serial port opens.
            self.serial_port = ser
            self.port = port
            self._drain_input()
            lines = self.send_command("ID?", wait=0.3)
            if not any(line in PUMP_DEVICE_IDS for line in lines):
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
                self.set_speed_mv(0)
            except Exception:
                pass
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None
        self.port = ""

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command: str, wait: float = 0.2) -> list[str]:
        if not self.is_connected():
            return ["ERR NOT_CONNECTED"]

        command = command.strip()
        if not command:
            return []

        with self.lock:
            self.serial_port.write((command + "\n").encode("ascii"))
            time.sleep(wait)
            return self.read_available_locked()

    def read_available(self) -> list[str]:
        if not self.is_connected():
            return []
        with self.lock:
            return self.read_available_locked()

    def read_available_locked(self) -> list[str]:
        lines: list[str] = []
        while self.serial_port and self.serial_port.in_waiting:
            raw = self.serial_port.readline()
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)
        return lines

    def _drain_input(self):
        if not self.is_connected():
            return
        deadline = time.time() + 0.5
        while time.time() < deadline:
            if not self.serial_port.in_waiting:
                time.sleep(0.05)
                continue
            self.serial_port.readline()

    def status(self) -> list[str]:
        return self.send_command("STATUS", wait=0.3)

    def stop(self) -> list[str]:
        return self.send_command("STOP", wait=0.2)

    def run(self, direction: str) -> list[str]:
        return self.send_command(f"RUN,{self._normalize_direction(direction)}", wait=0.2)

    def pulse(self, direction: str, duration_ms: int) -> list[str]:
        duration_ms = max(1, int(duration_ms))
        return self.send_command(
            f"PULSE,{self._normalize_direction(direction)},{duration_ms}",
            wait=0.2,
        )

    def set_direction(self, direction: str) -> list[str]:
        return self.send_command(f"DIR,{self._normalize_direction(direction)}", wait=0.2)

    def set_speed_mv(self, millivolts: int) -> list[str]:
        millivolts = max(0, min(5000, int(millivolts)))
        return self.send_command(f"SPEEDV,{millivolts}", wait=0.2)

    def set_speed_rpm(self, rpm: float) -> list[str]:
        rpm = max(0.0, float(rpm))
        return self.send_command(f"SPEED,{rpm:.2f}", wait=0.2)

    def set_max_rpm(self, rpm: float) -> list[str]:
        rpm = max(0.1, float(rpm))
        return self.send_command(f"MAXRPM,{rpm:.2f}", wait=0.2)

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        direction = (direction or "").strip().upper()
        if direction not in {"FWD", "REV"}:
            raise ValueError("Pump direction must be FWD or REV")
        return direction
