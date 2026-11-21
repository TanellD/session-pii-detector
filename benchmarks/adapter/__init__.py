"""Standalone mention adapter for benchmark detectors."""

from .adapter_contract import Mention, make_mention, CONTRACT_VERSION
from .adapter import extract

__all__ = ["Mention", "make_mention", "CONTRACT_VERSION", "extract"]
