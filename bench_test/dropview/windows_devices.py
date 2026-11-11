"""Resolve paired DropSens Bluetooth devices to their current outgoing COM ports."""
import re
import winreg

from serial.tools import list_ports

from bench_test.utils.debug_log import debug_log


_BTHENUM = r"SYSTEM\CurrentControlSet\Enum\BTHENUM"
_SPP_ADDRESS = re.compile(
    r"^BTHENUM\\\{00001101-0000-1000-8000-00805F9B34FB\}.*\\[^\\]+&([0-9A-F]{12})_C[0-9A-F]+$",
    re.IGNORECASE,
)


def list_dropsens_devices(ports=None):
    """Return (address, name, COM) tuples for Windows-listed UST devices."""
    devices = []
    for port in list_ports.comports() if ports is None else ports:
        match = _SPP_ADDRESS.fullmatch(port.hwid or "")
        if not match:
            continue
        address = match[1].upper()
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{_BTHENUM}\\Dev_{address}") as device:
                for index in range(winreg.QueryInfoKey(device)[0]):
                    instance = winreg.EnumKey(device, index)
                    try:
                        with winreg.OpenKey(device, instance) as key:
                            name = winreg.QueryValueEx(key, "FriendlyName")[0].strip()
                    except FileNotFoundError:
                        continue
                    if name.upper().startswith("UST"):
                        devices.append((address, name, port.device))
                        break
        except FileNotFoundError:
            continue
        except OSError as exc:
            debug_log(f"Could not read Bluetooth device for {port.device}: {exc}", level="warning")
    return sorted(devices, key=lambda device: (device[1], device[2]))
