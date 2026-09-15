"""Fault injection state for FakeBank.

Faults are set through the control API on a separate port (never reachable by
automation, because that origin is not in the policy allowlist).
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCREENS = {"menu", "member_inquiry", "member_detail", "member_transactions"}


@dataclass
class Faults:
    notice: str = "off"  # off | once | always
    supervisor_override: bool = False  # once, on the next member_detail render
    unknown_popup: bool = False  # once, on the next member_detail render
    latency: dict[str, int] = field(default_factory=dict)  # screen -> ms (persistent)
    session_expire_after: int | None = None  # requests allowed before the session expires
    error500: set[str] = field(default_factory=set)  # once per screen
    unauthorized: set[str] = field(default_factory=set)  # once per screen

    def set(self, key: str, value: str) -> None:
        if key == "notice":
            if value not in {"off", "once", "always"}:
                raise ValueError("notice must be off|once|always")
            self.notice = value
        elif key == "supervisor_override":
            self.supervisor_override = value == "once"
        elif key == "unknown_popup":
            self.unknown_popup = value == "once"
        elif key == "session_expire_after":
            self.session_expire_after = int(value)
        elif key.startswith("latency."):
            self.latency[_screen(key)] = int(value)
        elif key.startswith("error500."):
            self.error500.add(_screen(key))
        elif key.startswith("unauthorized."):
            self.unauthorized.add(_screen(key))
        else:
            raise ValueError(f"unknown fault {key!r}")

    def clear(self) -> None:
        self.__init__()

    def as_dict(self) -> dict:
        return {
            "notice": self.notice,
            "supervisor_override": self.supervisor_override,
            "unknown_popup": self.unknown_popup,
            "latency": self.latency,
            "session_expire_after": self.session_expire_after,
            "error500": sorted(self.error500),
            "unauthorized": sorted(self.unauthorized),
        }

    # consumption helpers, called while rendering a screen
    def take_notice(self) -> bool:
        if self.notice == "always":
            return True
        if self.notice == "once":
            self.notice = "off"
            return True
        return False

    def take_once(self, name: str, screen: str) -> bool:
        bucket: set[str] = getattr(self, name)
        if screen in bucket:
            bucket.discard(screen)
            return True
        return False

    def take_flag(self, name: str) -> bool:
        if getattr(self, name):
            setattr(self, name, False)
            return True
        return False

    def session_should_expire(self) -> bool:
        if self.session_expire_after is None:
            return False
        if self.session_expire_after <= 0:
            self.session_expire_after = None
            return True
        self.session_expire_after -= 1
        return False


def _screen(key: str) -> str:
    screen = key.split(".", 1)[1]
    if screen not in SCREENS:
        raise ValueError(f"unknown screen {screen!r}; expected one of {sorted(SCREENS)}")
    return screen
