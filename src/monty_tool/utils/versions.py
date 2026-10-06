"""
Storage for seeds / hashes to improve reproducibility.
"""

# Imports

from enum import StrEnum


# Seed

SEED = 2026


# Model enum

class FrozenModel(StrEnum):
    """
    A model with a revision hash under `.revision`.
    """
    MINILM_V6 = 'sentence-transformers/all-MiniLM-L6-v2'
    MS_MARCO_MINILM_L6_V2 = 'cross-encoder/ms-marco-MiniLM-L6-v2'
    QWEN3_1_7B = 'Qwen/Qwen3-1.7B'
    QWEN3_5_4B = 'Qwen/Qwen3.5-4B'
    QWEN3_5_9B = 'Qwen/Qwen3.5-9B'
    GEMMA4_E4B = 'google/gemma-4-E4B-it'

    @property
    def revision(self) -> str:
        """
        The stored hash for the selected model.
        """
        if self is FrozenModel.MINILM_V6:
            return '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
        if self is FrozenModel.MS_MARCO_MINILM_L6_V2:
            return '233902d25c440f23af6f7d6e94d2946bac0bee0a'
        if self is FrozenModel.QWEN3_1_7B:
            return '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
        if self is FrozenModel.QWEN3_5_4B:
            return '851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a'
        if self is FrozenModel.QWEN3_5_9B:
            return 'c202236235762e1c871ad0ccb60c8ee5ba337b9a'
        if self is FrozenModel.GEMMA4_E4B:
            return 'ee0ef6023621cff504d758262d4e04895a5af4a2'
        raise ValueError('No revision available!')
