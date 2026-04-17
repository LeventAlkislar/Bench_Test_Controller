# bench_test/valve/base_controller.py
import threading
import time
from typing import Optional

import serial

from bench_test.valve.protocol import SV01Protocol


class BaseValveController:
    """
    ValveController ve InjectorValveController'ın ortak kodları burada.
    Her iki sınıf da bu base'den türeyecek.
    """
    SERIAL_TIMEOUT = 5.0

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self.address = 0x00
        self.lock = threading.Lock()

    def connect(self, port, baudrate=9600, timeout=None):
        if timeout is None:
            timeout = self.SERIAL_TIMEOUT
        try:
            self.serial_port = serial.Serial(
                port=port, baudrate=baudrate,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE, timeout=timeout
            )
            return True
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to open serial port: {e}")

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
        self.serial_port = None

    def is_connected(self) -> bool:
        return self.serial_port is not None and self.serial_port.is_open

    def send_command(self, command, param1=0, param2=0, is_movement_cmd=False):
        if not self.is_connected():
            return {"success": False, "error": "Not connected"}
        with self.lock:
            try:
                cmd = SV01Protocol.build_command(self.address, command, param1, param2)
                self.serial_port.reset_input_buffer()
                self.serial_port.write(cmd)
                response = self.serial_port.read(8)
                if len(response) == 0:
                    if is_movement_cmd:
                        return {
                            "success": True,
                            "status": SV01Protocol.STATUS_TASK_SUSPENDED,
                            "status_message": "Command sent (motor busy)",
                            "timeout": True,
                            "raw": "N/A"
                        }
                    return {"success": False, "error": "No response (timeout)"}
                return SV01Protocol.parse_response(response)
            except serial.SerialException as e:
                return {"success": False, "error": f"Serial error: {e}"}

    def send_factory_command(self, command, p1=0, p2=0, p3=0, p4=0):
        if not self.is_connected():
            return {"success": False, "error": "Not connected"}
        with self.lock:
            try:
                cmd = SV01Protocol.build_factory_command(
                    self.address, command, p1, p2, p3, p4
                )
                self.serial_port.reset_input_buffer()
                self.serial_port.write(cmd)
                response = self.serial_port.read(8)
                if len(response) == 0:
                    return {"success": False, "error": "No response"}
                return SV01Protocol.parse_response(response)
            except serial.SerialException as e:
                return {"success": False, "error": f"Serial error: {e}"}

    def test_connection(self):
        return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def wait_for_completion(self, timeout=20.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            r = self.send_command(SV01Protocol.CMD_POLL_STATUS)
            if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
                return True
            if r.get("status") in [
                SV01Protocol.STATUS_TASK_SUSPENDED,
                SV01Protocol.STATUS_MOTOR_BUSY
            ]:
                time.sleep(0.2)
                continue
            time.sleep(0.1)
        return False