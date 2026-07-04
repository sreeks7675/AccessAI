"""
Orchestrator pipeline: DOM payload -> agents -> critique -> fix engine -> report.
Owned by Mahesh.
"""

from backend.orchestrator.contracts import DOMPayload, ReportJSON


class AuditPipeline:
    def __init__(self):
        # TODO Day 4: init connections to agents, vector store, fix engine
        pass

    async def run_audit(self, payload: DOMPayload) -> ReportJSON:
        """
        Full audit flow:
        1. Send DOM to 5 disability agents in parallel (Day 4)
        2. Pass agent findings through critique agent (Day 5)
        3. Send confirmed findings to fix engine (Day 6)
        4. Assemble and return ReportJSON (Day 5-6)
        """
        raise NotImplementedError("Wired up starting Day 4")