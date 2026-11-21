from __future__ import annotations

from dataclasses import dataclass

from sherpy.data.source import CaseSource


@dataclass
class AgentDeps:
    case_id: str
    source: CaseSource
