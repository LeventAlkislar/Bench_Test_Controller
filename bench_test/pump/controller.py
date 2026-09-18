from bench_test.pump.arduino_pump import ArduinoPumpController
from bench_test.pump.flexicon_pump import FlexiconPumpController


PUMP_TYPE_ARDUINO = "arduino"
PUMP_TYPE_FLEXICON = "flexicon"


class PumpController:
    """Shared pump facade used by manual controls and recipe execution."""

    def __init__(self):
        self._controllers = {
            PUMP_TYPE_ARDUINO: ArduinoPumpController(),
            PUMP_TYPE_FLEXICON: FlexiconPumpController(),
        }
        self._active_type = PUMP_TYPE_ARDUINO

    @property
    def active_type(self) -> str:
        return self._active_type

    @property
    def port(self) -> str:
        return self._active.port

    @property
    def model_name(self) -> str:
        if self._active_type == PUMP_TYPE_FLEXICON:
            return "Flexicon 520Di"
        return "Arduino Nano"

    @property
    def _active(self):
        return self._controllers[self._active_type]

    def select_type(self, pump_type: str):
        if pump_type not in self._controllers:
            raise ValueError(f"Unknown pump type: {pump_type}")
        if pump_type == self._active_type:
            return
        if self.is_connected():
            self.disconnect()
        self._active_type = pump_type

    def configure_flexicon(self, pump_id: int, baudrate: int = 9600):
        self._controllers[PUMP_TYPE_FLEXICON].configure(pump_id=pump_id, baudrate=baudrate)

    def connect(self, port: str, *args, **kwargs) -> bool:
        return self._active.connect(port, *args, **kwargs)

    def disconnect(self, *args, **kwargs):
        return self._active.disconnect(*args, **kwargs)

    def is_connected(self) -> bool:
        return self._active.is_connected()

    def send_command(self, *args, **kwargs) -> list[str]:
        return self._active.send_command(*args, **kwargs)

    def read_available(self) -> list[str]:
        return self._active.read_available()

    def status(self) -> list[str]:
        return self._active.status()

    def stop(self) -> list[str]:
        return self._active.stop()

    def run(self, direction: str) -> list[str]:
        return self._active.run(direction)

    def pulse(self, direction: str, duration_ms: int) -> list[str]:
        return self._active.pulse(direction, duration_ms)

    def set_direction(self, direction: str) -> list[str]:
        return self._active.set_direction(direction)

    def set_speed_mv(self, millivolts: int) -> list[str]:
        return self._active.set_speed_mv(millivolts)

    def set_speed_rpm(self, rpm: float) -> list[str]:
        return self._active.set_speed_rpm(rpm)

    def set_max_rpm(self, rpm: float) -> list[str]:
        return self._active.set_max_rpm(rpm)
