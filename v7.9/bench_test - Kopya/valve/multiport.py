# bench_test/valve/multiport.py
from bench_test.valve.base_controller import BaseValveController
from bench_test.valve.protocol import SV01Protocol


class ValveController(BaseValveController):
    """
    SV-01 Multiport Valve (8-port selector) kontrolcüsü.
    Ortak kodlar BaseValveController'da, burada sadece
    bu valfe özgü metodlar var.
    """
    MIN_SPEED     = 5
    MAX_SPEED     = 350
    DEFAULT_SPEED = 200

    def __init__(self):
        super().__init__()
        self.current_speed = self.DEFAULT_SPEED

    def switch_port(self, port):
        return self.send_command(SV01Protocol.CMD_SWITCH_PORT, port, 0, True)

    def reset(self):
        return self.send_command(SV01Protocol.CMD_RESET, is_movement_cmd=True)

    def stop(self):
        return self.send_command(SV01Protocol.CMD_STRONG_STOP)

    def get_current_port(self):
        return self.send_command(SV01Protocol.CMD_QUERY_PORT)

    def get_status(self):
        return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def get_max_speed(self):
        r = self.send_command(SV01Protocol.CMD_QUERY_MAX_SPEED)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            r["speed_rpm"] = r.get("param_value", 0)
        return r

    def set_max_speed(self, speed_rpm):
        if not self.MIN_SPEED <= speed_rpm <= self.MAX_SPEED:
            return {"success": False, "error": f"Speed must be {self.MIN_SPEED}-{self.MAX_SPEED}"}
        p1 = speed_rpm & 0xFF
        p2 = (speed_rpm >> 8) & 0xFF
        r = self.send_factory_command(SV01Protocol.CMD_SET_MAX_SPEED, p1, p2)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.current_speed = speed_rpm
        return r

    def set_speed_dynamic(self, speed_rpm):
        if not self.MIN_SPEED <= speed_rpm <= self.MAX_SPEED:
            return {"success": False, "error": f"Speed must be {self.MIN_SPEED}-{self.MAX_SPEED}"}
        p1 = speed_rpm & 0xFF
        p2 = (speed_rpm >> 8) & 0xFF
        r = self.send_command(SV01Protocol.CMD_SET_SPEED_DYNAMIC, p1, p2)
        if r.get("success") and r.get("status") == SV01Protocol.STATUS_NORMAL:
            self.current_speed = speed_rpm
        return r