"""Bounded, owned Windows Update pause policy; never claims an unverified pause.

Microsoft documents these policy values and effective status keys at:
https://learn.microsoft.com/windows/deployment/update/waas-configure-wufb
IAutomaticUpdates.Pause is deliberately not used (a no-op on Windows 10+).
"""
import threading
from datetime import datetime, timedelta, timezone

from bench_test.watchdog.state import ProcessLock, atomic_write, read_json


POLICY = r"SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
EFFECTIVE = r"SOFTWARE\Microsoft\WindowsUpdate\UpdatePolicy\Settings"
PAUSE_NAMES = ("PauseQualityUpdatesStartTime", "PauseFeatureUpdatesStartTime")
REBOOT_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
)


class WindowsRegistry:
    def read(self, path, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value, kind = winreg.QueryValueEx(key, name)
                return {"value": value, "kind": kind}
        except FileNotFoundError:
            return None

    def write(self, path, name, value):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.SetValueEx(key, name, 0, value["kind"], value["value"])

    def delete(self, path, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass

    def exists(self, path):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                return True
        except FileNotFoundError:
            return False


class WindowsUpdateGuard:
    def __init__(self, directory, run_id, registry=None, now=None):
        self.directory, self.run_id = directory, run_id
        self.registry = registry or WindowsRegistry()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.cancelled = threading.Event()

    def status(self, required_until=None):
        backup = read_json(self.directory / "update_policy.json")
        if backup and not backup.get("closed") and backup.get("run_id") != self.run_id:
            return "owned_by_previous_run: previous pause has not been restored"
        if any(self.registry.exists(path) for path in REBOOT_KEYS):
            return "pending_reboot: Windows already requires a restart"
        required_until = required_until or self.now()
        ends = []
        for kind in ("Quality", "Feature"):
            status = self.registry.read(EFFECTIVE, f"Paused{kind}Status")
            date = self.registry.read(EFFECTIVE, f"Paused{kind}Date")
            policy_date = self.registry.read(POLICY, f"Pause{kind}UpdatesStartTime")
            if not status or status["value"] != 1 or not date:
                return "not_verified: Windows has not confirmed both update pauses"
            if not policy_date or str(policy_date["value"])[:10] != str(date["value"])[:10]:
                return "not_verified: effective pause date does not match a known 35-day policy"
            try:
                start = datetime.fromisoformat(str(date["value"]).replace("Z", "+00:00"))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
            except ValueError:
                return "not_verified: pause expiry could not be read"
            ends.append(start + timedelta(days=35))
        expiry = min(ends)
        if expiry < required_until:
            return "insufficient_pause: existing pause does not cover the experiment"
        return "paused_verified_until:" + expiry.isoformat()

    def protect(self, required_days=15):
        if required_days > 35:
            return "unsupported_duration: Windows pause is limited to 35 days"
        try:
            with ProcessLock(self.directory / "update_policy.lock"):
                if self.cancelled.is_set():
                    return "cancelled"
                required_until = self.now() + timedelta(days=required_days)
                effective = self.status(required_until)
                if effective.startswith(("paused_verified", "pending_reboot")):
                    return effective
                edition = self.registry.read(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "EditionID")
                if not edition or edition["value"] not in ("Professional", "ProfessionalEducation", "ProfessionalWorkstation", "Enterprise", "Education", "EnterpriseS"):
                    return "unsupported_edition: pause updates in Windows Settings"
                backup = read_json(self.directory / "update_policy.json")
                if backup and not backup.get("closed"):
                    if backup["run_id"] != self.run_id:
                        return "owned_by_previous_run: previous pause has not been restored"
                    return effective
                originals = {name: self.registry.read(POLICY, name) for name in PAUSE_NAMES}
                if any(value is not None for value in originals.values()):
                    return "external_policy: existing update policy was preserved; " + effective
                applied = {"value": self.now().date().isoformat(), "kind": 1}  # REG_SZ
                backup = {"run_id": self.run_id, "originals": originals, "applied": applied, "closed": False}
                # Persist ownership before the first system change, including partial writes.
                atomic_write(self.directory / "update_policy.json", backup)
                try:
                    for name in PAUSE_NAMES:
                        if self.cancelled.is_set():
                            self._restore_locked(backup)
                            return "cancelled"
                        self.registry.write(POLICY, name, applied)
                except OSError:
                    self._restore_locked(backup)
                    raise
                return "pause_requested; " + self.status(required_until)
        except PermissionError:
            return "needs_admin: update pause could not be applied"
        except (OSError, ValueError, RuntimeError) as exc:
            return "pause_unavailable: " + str(exc)

    def _restore_locked(self, backup):
        for name, original in backup["originals"].items():
            # Never undo a policy edit made by Windows, IT or the user afterwards.
            if self.registry.read(POLICY, name) != backup["applied"]:
                continue
            if original is None:
                self.registry.delete(POLICY, name)
            else:
                self.registry.write(POLICY, name, original)
        backup["closed"] = True
        atomic_write(self.directory / "update_policy.json", backup)

    def restore(self):
        self.cancelled.set()
        try:
            with ProcessLock(self.directory / "update_policy.lock"):
                backup = read_json(self.directory / "update_policy.json")
                if backup and not backup.get("closed") and backup["run_id"] == self.run_id:
                    self._restore_locked(backup)
            return "previous_update_policy_restored"
        except (OSError, ValueError, RuntimeError) as exc:
            return "restore_unavailable: " + str(exc)
