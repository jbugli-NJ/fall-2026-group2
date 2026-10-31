"""
Shared interface for judging the contribution of external data.
"""

# Imports

from typing import Protocol

from monty_tool.gap_analysis.schemas import Contribution, PairedResult


# Protocols

class ContributionJudge(Protocol):
    """
    Judge the contribution of external data in an answer pair.
    """

    def judge(self, pair: PairedResult) -> Contribution:
        """
        Judge the contribution provided by external data in the given result pair.
        """
        ...
