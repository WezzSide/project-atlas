"""Expected input failures for operator-facing CLI (no tracebacks)."""

from __future__ import annotations


class ImprovementPlaneError(ValueError):
    """User-correctable improvement-plane input/state error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"ok": "false", "error_code": self.code, "error": str(self)}
