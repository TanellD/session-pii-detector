"""Явный benchmark bypass и fallback с неполнотой, а не фиктивной уверенностью."""
import os
import time

from .components import LinkResult
from .observability import emit


class BypassLinker:
    context_window = 0

    def __init__(self, delay_ms=0):
        if not 0 <= delay_ms <= 60000:
            raise ValueError('Benchmark linker delay must be between 0 and 60000 ms')
        self.delay_ms = delay_ms

    def link(self, message, candidates, known_facts):
        if self.delay_ms:
            time.sleep(self.delay_ms/1000)
        emit('linker_bypassed', candidates=len(candidates), simulated_delay_ms=self.delay_ms)
        return LinkResult(tuple(None for _ in candidates), complete=False, reasons=('linker_bypassed_benchmark',))


class FallbackLinker:
    def __init__(self, primary):
        self.primary = primary
        self.context_window = getattr(primary, 'context_window', 0)

    def link(self, message, candidates, known_facts):
        return self.link_with_context(message, candidates, known_facts, ())

    def link_with_context(self, message, candidates, known_facts, previous_messages):
        try:
            contextual = getattr(self.primary, 'link_with_context', None)
            result = (contextual(message, candidates, known_facts, previous_messages) if contextual else
                      self.primary.link(message, candidates, known_facts))
            if len(result.person_ids) != len(candidates) or not result.complete:
                raise ValueError('Incomplete primary linker')
            return result
        except Exception as error:
            reason = 'timeout' if isinstance(error, TimeoutError) else 'invalid_response' if isinstance(error, ValueError) else 'component_failure'
            emit('linker_fallback', candidates=len(candidates), fallback='unknown', reason=reason)
            return LinkResult(tuple(None for _ in candidates), complete=False, reasons=('linker_fallback_unknown',))


def runtime_linker():
    from .gemma_linker import build_linker
    fallback = os.environ.get('PII_LINKER_FALLBACK', 'error')
    if fallback not in {'error', 'unknown'}:
        raise ValueError('PII_LINKER_FALLBACK must be error or unknown')
    primary = (BypassLinker(float(os.environ.get('PII_BENCHMARK_LINKER_DELAY_MS', '0')))
               if os.environ.get('PII_LINKER_MODE') == 'bypass' else build_linker())
    return FallbackLinker(primary) if fallback == 'unknown' and not isinstance(primary, BypassLinker) else primary
