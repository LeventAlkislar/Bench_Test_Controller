# bench_test/valve/protocol.py


class SV01Protocol:
    START_CODE = 0xCC
    END_CODE   = 0xDD
    PASSWORD   = [0xFF, 0xEE, 0xBB, 0xAA]

    CMD_SWITCH_PORT       = 0x44
    CMD_RESET             = 0x45
    CMD_STRONG_STOP       = 0x49
    CMD_POLL_STATUS       = 0x4A
    CMD_SET_SPEED_DYNAMIC = 0x4B
    CMD_QUERY_PORT        = 0x3E
    CMD_QUERY_MAX_SPEED   = 0x27
    CMD_QUERY_RESET_SPEED = 0x2B
    CMD_SET_MAX_SPEED     = 0x07
    CMD_SET_RESET_SPEED   = 0x0B

    STATUS_NORMAL            = 0x00
    STATUS_FRAME_ERROR       = 0x01
    STATUS_PARAM_ERROR       = 0x02
    STATUS_OPTOCOUPLER_ERROR = 0x03
    STATUS_MOTOR_BUSY        = 0x04
    STATUS_TASK_SUSPENDED    = 0xFE
    STATUS_UNKNOWN_ERROR     = 0xFF

    STATUS_MESSAGES = {
        0x00: "Normal",
        0x01: "Frame Error",
        0x02: "Parameter Error",
        0x03: "Optocoupler Error",
        0x04: "Motor Busy",
        0xFE: "Task Suspended (Motor Working)",
        0xFF: "Unknown Error",
    }

    @staticmethod
    def calculate_checksum(data: bytes) -> int:
        return sum(data) & 0xFFFF

    @classmethod
    def build_command(cls, address, command, param1=0, param2=0):
        frame = bytes([cls.START_CODE, address, command, param1, param2, cls.END_CODE])
        cs = cls.calculate_checksum(frame)
        return frame + bytes([cs & 0xFF, (cs >> 8) & 0xFF])

    @classmethod
    def build_factory_command(cls, address, command, p1=0, p2=0, p3=0, p4=0):
        frame = bytes([
            cls.START_CODE, address, command,
            cls.PASSWORD[0], cls.PASSWORD[1], cls.PASSWORD[2], cls.PASSWORD[3],
            p1, p2, p3, p4, cls.END_CODE
        ])
        cs = cls.calculate_checksum(frame)
        return frame + bytes([cs & 0xFF, (cs >> 8) & 0xFF])

    @classmethod
    def parse_response(cls, response: bytes) -> dict:
        if len(response) < 8:
            return {"success": False, "error": f"Response too short ({len(response)} bytes)"}
        if response[0] != cls.START_CODE or response[5] != cls.END_CODE:
            return {"success": False, "error": "Invalid frame markers"}
        expected = cls.calculate_checksum(response[:6])
        actual   = response[6] | (response[7] << 8)
        if expected != actual:
            return {"success": False, "error": "Checksum mismatch"}
        status = response[2]
        return {
            "success":      True,
            "address":      response[1],
            "status":       status,
            "status_message": cls.STATUS_MESSAGES.get(status, "Unknown"),
            "param1":       response[3],
            "param2":       response[4],
            "param_value":  response[3] | (response[4] << 8),
            "raw":          response.hex().upper(),
        }