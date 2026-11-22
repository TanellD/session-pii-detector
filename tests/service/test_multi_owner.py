"""Совместные владельцы через HTTP, состояние, риск и повтор запроса."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from fastapi.testclient import TestClient
from session_pii.app import create_app
from session_pii.components import DetectionResult, LinkResult
from session_pii.models import Span


class Detector:
    def detect(self, message):
        return DetectionResult((Span(type='phone' if message.message_id == 1 else 'email',
                                     start=0, end=len(message.text)),))


class Empty:
    def detect(self, message):
        return DetectionResult()


class Linker:
    def __init__(self, owners=('p1', 'p2'), service=False):
        self.owners, self.service = owners, service

    def link(self, message, candidates, known_facts):
        return LinkResult((self.owners if message.message_id == 1 else 'p1',),
                          service_contacts=(self.service,))


class MultiOwnerServiceTests(unittest.TestCase):
    def test_http_shared_owner_round_trip_retry_and_separate_risks(self):
        client = TestClient(create_app(ner=Detector(), regexp=Empty(), linker=Linker()))
        state = client.post('/v1/sessions', json={'session_id': 'joint-test'}).json()
        def payload(state, text):
            return dict(session_id=state['session_id'], session_epoch=state['session_epoch'],
                        expected_state_version=state['state_version'],
                        message=dict(message_id=state['next_message_id'], text=text))
        request = payload(state, '+7 (000) 000-00-01')
        response = client.post('/v1/analyze', json=request)
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual(len(state['entities']), 1)
        self.assertEqual(state['entities'][0]['person_id'], ['p1', 'p2'])
        for owner in ('p1', 'p2'):
            self.assertEqual(state['verdict']['person_risks'][owner]['risk_score'], 0.3)
        self.assertEqual(state['verdict']['unknown_risk']['risk_score'], 0)
        self.assertEqual(client.post('/v1/analyze', json=request).json(), state)
        response = client.post('/v1/analyze', json=payload(state, 'synthetic@example.invalid'))
        self.assertEqual(response.status_code, 200, response.text)
        verdict = response.json()['verdict']
        self.assertTrue(verdict['current_alert'])
        self.assertIn('combination_rule', verdict['person_risks']['p1']['rules'])
        self.assertEqual(verdict['person_risks']['p2']['risk_score'], 0.3)
        self.assertFalse(verdict['person_risks']['p2']['rules'])

    def test_invalid_linker_owners_and_service_contacts_rejected(self):
        from session_pii.pipeline import Pipeline
        from session_pii.models import Message, Verdict
        for owners, service in (((), False), (('p1', 'p1'), False),
                                (('p1', ''), False), (('p1', None), False),
                                (('p1', 'p2'), True)):
            with self.subTest(owners=owners, service=service), self.assertRaises(ValueError):
                Pipeline(Detector(), Empty(), Linker(owners, service)).process(
                    Message(message_id=1, text='+7 (000) 000-00-01'), (), Verdict())
