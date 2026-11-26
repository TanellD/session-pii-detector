"""Runtime v2, безопасные логи, явное ухудшение и readiness."""
import json
import os
from pathlib import Path
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from fastapi.testclient import TestClient
from session_pii.app import create_app
from session_pii.components import DetectionResult, LinkResult
from session_pii.detectors import DetectorSettings
from session_pii.linker_runtime import BypassLinker, FallbackLinker, runtime_linker
from session_pii.models import Message, Span
from session_pii.risk import RiskPolicy
from session_pii.gemma_linker import ChatCompletion, GemmaSettings


class DockerRuntimeTests(unittest.TestCase):
    def test_bypass_no_external_call_and_private_telemetry(self):
        marker = 'synthetic-private-marker'
        with patch.dict(os.environ, {'PII_LINKER_MODE': 'bypass', 'PII_LINKER_FALLBACK': 'error'}):
            with patch('session_pii.gemma_linker.ChatCompletion.__call__', side_effect=AssertionError('network called')):
                app = create_app()
                with self.assertLogs('session_pii.telemetry', level='INFO') as logs, TestClient(app) as client:
                    self.assertEqual(client.get('/readyz').status_code, 200)
                    state = client.post('/v1/sessions', json={'session_id': marker}).json()
                    payload = dict(session_id=marker, session_epoch=state['session_epoch'], expected_state_version=0,
                                   message=dict(message_id=1, text='Почта demo@example.invalid'))
                    result = client.post('/v1/analyze', json=payload, headers={'X-Request-ID': marker})
                    self.assertEqual(result.status_code, 200)
                    self.assertFalse(result.json()['verdict']['coverage_complete'])
                    self.assertIn('linker_bypassed_benchmark', result.json()['verdict']['incomplete_reasons'])
                    self.assertIn('ner;dur=', result.headers['Server-Timing'])
                    self.assertNotEqual(result.headers['X-Request-ID'], marker)
                recorded = '\n'.join(logs.output)
                self.assertNotIn(marker, recorded)
                self.assertNotIn('demo@example.invalid', recorded)
                self.assertNotIn(state['session_epoch'], recorded)
                parsed = [json.loads(r.getMessage()) for r in logs.records]
                self.assertTrue(any(r['event'] == 'linker_bypassed' for r in parsed))
                self.assertTrue(any(r.get('route') == '/v1/analyze' for r in parsed))

    def test_fallback_marks_analysis_incomplete_and_does_not_log_error_text(self):
        class Failing:
            context_window = 3
            def link(self, *args):
                raise TimeoutError('synthetic-secret-value')
        fallback = FallbackLinker(Failing())
        with self.assertLogs('session_pii.telemetry') as logs:
            value = fallback.link(Message(message_id=1, text='Тест'), (Span(type='person', start=0, end=4),), ())
        self.assertEqual(value.person_ids, (None,))
        self.assertFalse(value.complete)
        self.assertEqual(fallback.context_window, 3)
        self.assertNotIn('synthetic-secret-value', '\n'.join(logs.output))

    def test_strict_mode_does_not_hide_linker_failure(self):
        with patch.dict(os.environ, {'PII_LINKER_MODE': 'stub', 'PII_LINKER_FALLBACK': 'error'}):
            self.assertNotIsInstance(runtime_linker(), FallbackLinker)

    def test_environment_selects_v2_and_explicit_candidate_policy(self):
        with patch.dict(os.environ, {'PII_ADAPTER_REVISION': 'v2', 'PII_DETECTOR_MODE': 'regex',
                                    'PII_RISK_POLICY': str(ROOT/'configs/runtime-risk-tuned-validation.json')}):
            self.assertEqual(DetectorSettings.from_env().adapter_revision, 'v2')
            self.assertEqual(RiskPolicy.load().person_threshold, .35)
            self.assertEqual(RiskPolicy.load().unknown_threshold, .6)

    def test_readiness_waits_for_initialization(self):
        class Detector:
            initialized = False
            def initialize(self):
                self.initialized = True
            def detect(self, message):
                return DetectionResult()
        detector = Detector()
        app = create_app(ner=detector, linker=BypassLinker())
        self.assertFalse(detector.initialized)
        with TestClient(app) as client:
            self.assertTrue(detector.initialized)
            self.assertEqual(client.get('/readyz').status_code, 200)

    def test_overload_rejected_without_running_second_pipeline(self):
        entered, release = Event(), Event()
        class BlockingDetector:
            def detect(self, message):
                entered.set()
                release.wait(3)
                return DetectionResult()
        with patch.dict(os.environ, {'PII_MAX_INFLIGHT': '1'}):
            app = create_app(ner=BlockingDetector(), linker=BypassLinker())
            with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
                state = client.post('/v1/sessions', json={'session_id': 'synthetic-overload'}).json()
                payload = dict(session_id=state['session_id'], session_epoch=state['session_epoch'],
                    expected_state_version=0, message=dict(message_id=1, text='Проверка'))
                future = executor.submit(client.post, '/v1/analyze', json=payload)
                try:
                    self.assertTrue(entered.wait(2))
                    result = client.post('/v1/analyze', json=payload)
                    self.assertEqual(result.status_code, 503)
                    self.assertEqual(result.json()['error']['code'], 'OVERLOADED')
                finally:
                    release.set()
                self.assertEqual(future.result().status_code, 200)

    def test_linker_capacity_wait_has_timeout_without_network(self):
        client = ChatCompletion(GemmaSettings(api_key='synthetic-unit', timeout_seconds=.01))
        client.semaphore.acquire()
        with patch('session_pii.gemma_linker.build_opener', side_effect=AssertionError('network called')):
            with self.assertRaises(TimeoutError):
                client({})


if __name__ == '__main__':
    unittest.main()
