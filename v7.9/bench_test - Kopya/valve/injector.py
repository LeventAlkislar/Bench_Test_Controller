# bench_test/valve/injector.py
from bench_test.valve.base_controller import BaseValveController
from bench_test.valve.protocol import SV01Protocol


class InjectorValveController(BaseValveController):
    """
    SY-07B Injector Valve (6-port, 2-state) kontrolcüsü.
    Ortak kodlar BaseValveController'da, burada sadece
    bu valfe özgü metodlar var.
    """
    STATE_LOAD   = 1
    STATE_INJECT = 2
    STATE_NAMES  = {
        1: "Load (1-6, 2-3, 4-5)",
        2: "Inject (1-2, 3-4, 5-6)",
    }

    def __init__(self):
        super().__init__()
        self.current_state = 0

    def disconnect(self):
        super().disconnect()
        self.current_state = 0

    def switch_state(self, state):
        if state not in [self.STATE_LOAD, self.STATE_INJECT]:
            return {"success": False, "error": "State must be 1 or 2"}
        r = self.send_command(SV01Protocol.CMD_SWITCH_PORT, state, 0, True)
        if r.get("success"):
            self.current_state = state
        return r

    def set_load_position(self):
        return self.switch_state(self.STATE_LOAD)

    def set_inject_position(self):
        return self.switch_state(self.STATE_INJECT)

    def reset(self):
        r = self.send_command(SV01Protocol.CMD_RESET, is_movement_cmd=True)
        if r.get("success"):
            self.current_state = self.STATE_INJECT
        return r

    def stop(self):
        return self.send_command(SV01Protocol.CMD_STRONG_STOP)

    def get_status(self):
        return self.send_command(SV01Protocol.CMD_POLL_STATUS)

    def get_current_state(self):
        r = self.send_command(SV01Protocol.CMD_QUERY_PORT)
        if r.get("success"):
            s = r.get("param1", 0)
            r["state"] = s
            r["state_name"] = self.STATE_NAMES.get(s, "Unknown")
            self.current_state = s
        return r