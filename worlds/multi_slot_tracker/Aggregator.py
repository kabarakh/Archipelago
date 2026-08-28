"""Pure aggregation over a list of SlotLogicResult -- no network/logic dependency, see design doc
section 4.6. Kept separate from LogicEngine so it stays trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .LogicEngine import SlotLogicResult


@dataclass
class DashboardData:
    generated_at: datetime
    slots: list[SlotLogicResult] = field(default_factory=list)

    @property
    def total_open(self) -> int:
        return sum(s.total_locations - s.checked for s in self.slots)

    @property
    def total_in_logic(self) -> int:
        return sum(s.in_logic_open or 0 for s in self.slots if s.error is None)

    @property
    def restricted_count(self) -> int:
        """Slots where no logic numbers could be computed at all (compat == "yaml_required" or
        "unknown_game", or any other hard error)."""
        return sum(1 for s in self.slots if s.error is not None)

    @staticmethod
    def build(slots: list[SlotLogicResult]) -> "DashboardData":
        return DashboardData(generated_at=datetime.now(timezone.utc), slots=slots)
