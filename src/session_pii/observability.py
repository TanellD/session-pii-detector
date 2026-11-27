"""Метаданные и таймеры без текстов, значений фактов, ID сессий и исключений."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import sys
import time

context = ContextVar('pii_telemetry', default=None)
logger = logging.getLogger('session_pii.telemetry')


def configure():
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
        logger.propagate = False


def emit(event, **fields):
    configure()
    # Call sites supply fixed keys and aggregate values, never user input.
    state = context.get()
    if (state is not None and not state.get('sampled', True)
            and event in {'http_request', 'analysis_result', 'linker_bypassed'} and fields.get('status', 200) < 400):
        return
    record = dict(timestamp=datetime.now(timezone.utc).isoformat(), event=event,
                  request_id=state['request_id'] if state else None, **fields)
    logger.info(json.dumps(record, ensure_ascii=True, allow_nan=False))


@contextmanager
def timed(stage):
    start = time.perf_counter()
    try:
        yield
    except Exception:
        if stage != 'analysis':
            emit('component_error', stage=stage)
        raise
    finally:
        state = context.get()
        if state is not None:
            state['stages_ms'][stage] = state['stages_ms'].get(stage, 0)+(time.perf_counter()-start)*1000
