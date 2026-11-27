"""Сквозные проверки адаптера и сервисного обработчика на синтетике."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from fastapi.testclient import TestClient
from session_pii.app import create_app
from session_pii.components import DetectionResult, EmptyDetector, Fact, LinkResult
from session_pii.detectors import AdapterDetector, DetectorSettings
from session_pii.models import Message, Span
from session_pii.risk import RiskEvaluator


class FixedLinker:
    def link(self, message, candidates, known_facts):
        return LinkResult(tuple('p1' for _ in candidates))


class AdapterIntegrationTests(unittest.TestCase):
    def client(self, detector=None, linker=None):
        app = create_app(ner=detector, regexp=EmptyDetector() if detector else None,
                         linker=linker)
        client = TestClient(app)
        state = client.post('/v1/sessions', json={'session_id': 'adapter-synthetic'}).json()
        return client, state

    def send(self, client, state, text):
        request = dict(session_id=state['session_id'], session_epoch=state['session_epoch'],
                       expected_state_version=state['state_version'],
                       message=dict(message_id=state['next_message_id'], text=text))
        response = client.post('/v1/analyze', json=request)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), request

    def injected(self, fragments):
        def predict(text):
            return [dict(label=label, start=text.index(value), end=text.index(value)+len(value), score=.91)
                    for value, label in fragments if value in text]
        return AdapterDetector(DetectorSettings(mode='gliner', model_dir=Path('unused-test-model')),
                               predictor=predict)

    def test_default_regex_extracts_without_claiming_full_coverage(self):
        client, state = self.client()
        response, request = self.send(client, state, 'Моя почта synthetic@example.invalid.')
        self.assertEqual([e['type'] for e in response['entities']], ['email'])
        self.assertEqual(response['entities'][0]['sources'], ['regex'])
        self.assertEqual(response['entities'][0]['provenance'][0]['confidence_kind'], 'none')
        self.assertIsNone(response['entities'][0]['provenance'][0]['confidence'])
        self.assertEqual(set(response['verdict']['incomplete_reasons']),
                         {'ner_not_configured', 'linker_not_configured'})
        self.assertEqual(client.post('/v1/analyze', json=request).json(), response)

    def test_context_rule_preserves_model_name_and_its_provenance(self):
        detector = self.injected([('Тестимир', 'person')])
        client, state = self.client(detector, FixedLinker())
        response, _ = self.send(client, state, 'Тестимир, фамилия Макетонов.')
        self.assertEqual([e['type'] for e in response['entities']], ['person', 'last_name'])
        self.assertEqual(response['entities'][0]['provenance'][0]['source'], 'gliner')
        self.assertEqual(response['entities'][1]['provenance'][0]['source'], 'regex')
        self.assertTrue(response['verdict']['coverage_complete'])
        self.assertEqual(response['verdict']['person_risks']['p1']['risk_score'], .2)

    def test_organization_suppresses_nested_generic_person(self):
        detector = self.injected([('Макетонов', 'person')])
        client, state = self.client(detector, FixedLinker())
        response, _ = self.send(client, state, 'ООО «Макетонов»')
        self.assertEqual([e['type'] for e in response['entities']], ['organization'])
        self.assertEqual(response['suppressed'][0]['reason'], 'name_inside_organization')
        self.assertEqual(response['verdict']['person_risks']['p1']['risk_score'], .1)

    def test_organization_suppresses_name_parts_inside_its_title(self):
        class Detector:
            def detect(self, message):
                return DetectionResult((Span(type='organization', start=0, end=len(message.text)),
                                        Span(type='last_name', start=5, end=14)))
        client, state = self.client(Detector(), FixedLinker())
        response, _ = self.send(client, state, 'ООО «Макетонов»')
        self.assertEqual([e['type'] for e in response['entities']], ['organization'])
        self.assertEqual(response['suppressed'][0]['reason'], 'name_inside_organization')

    def test_address_suppresses_nested_geography(self):
        text = 'г. Макетоград, ул. Пробная, д. 1'
        class Detector:
            def detect(self, message):
                return DetectionResult((Span(type='address', start=0, end=len(text)),
                    Span(type='geography', start=3, end=13)))
        client, state = self.client(Detector(), FixedLinker())
        response, _ = self.send(client, state, text)
        self.assertEqual([e['type'] for e in response['entities']], ['address'])
        self.assertEqual(response['suppressed'][0]['reason'], 'geography_inside_address')

    def test_model_person_inside_email_does_not_add_name_risk(self):
        detector = self.injected([('synthetic', 'person')])
        client, state = self.client(detector, FixedLinker())
        response, _ = self.send(client, state, 'Почта synthetic@example.invalid.')
        self.assertEqual([e['type'] for e in response['entities']], ['email'])
        self.assertEqual(response['verdict']['person_risks']['p1']['risk_score'], .3)

    def test_adapter_runs_regex_only_once_and_retry_does_not_repeat_model(self):
        calls = []
        detector = AdapterDetector(DetectorSettings(mode='gliner', model_dir=Path('unused-test-model')),
                                   predictor=lambda text: calls.append(text) or [])
        client, state = self.client(detector, FixedLinker())
        response, request = self.send(client, state, 'Моя почта synthetic@example.invalid.')
        self.assertEqual(len(response['entities']), 1)
        self.assertEqual(client.post('/v1/analyze', json=request).json(), response)
        self.assertEqual(len(calls), 1)

    def test_shared_adapter_ids_are_scoped_by_message_epoch_and_session(self):
        client, state = self.client(AdapterDetector(), FixedLinker())
        first, _ = self.send(client, state, 'a@example.invalid')
        second, _ = self.send(client, first, 'b@example.invalid')
        a, b = first['entities'][0], second['entities'][0]
        self.assertEqual(a['provenance'][0]['mention_id'], b['provenance'][0]['mention_id'])
        self.assertNotEqual(a['mention_keys'], b['mention_keys'])
        reset = client.post('/v1/sessions/adapter-synthetic/reset', json=dict(
            session_epoch=second['session_epoch'], expected_state_version=second['state_version'])).json()
        third, _ = self.send(client, reset, 'a@example.invalid')
        self.assertNotEqual(a['mention_keys'], third['entities'][0]['mention_keys'])
        self.assertEqual(third['entities'][0]['mention_keys'][0]['session_id'], 'adapter-synthetic')

    def test_generic_person_has_yellow_weight_without_forming_name_pair(self):
        facts = (Fact('person', 'Образимир Макетный', 'p1', 'e1', 1),
                 Fact('phone', '+7 (000) 000-00-01', 'p1', 'e2', 2))
        result = RiskEvaluator().assess(facts)
        self.assertEqual(result.risk_score, .4)
        self.assertFalse(result.rules)

    def test_generic_person_refinement_does_not_double_count(self):
        facts = (Fact('person', 'Макетонов Тестимир', 'p1', 'e1', 1),
                 Fact('last_name', 'Макетонов', 'p1', 'e2', 2),
                 Fact('first_name', 'Тестимир', 'p1', 'e3', 3))
        self.assertEqual(RiskEvaluator().assess(facts).risk_score, .3)
        self.assertEqual(len(facts), 3)

    def test_format_variants_of_unknown_phone_do_not_add_risk(self):
        facts = tuple(Fact('phone', value, None, f'e{i}', i+1) for i, value in enumerate(
            ('+7 (000) 000-00-01', '80000000001', '+70000000001')))
        result = RiskEvaluator().assess(facts, unknown=True)
        self.assertEqual(result.risk_score, .3)
        self.assertFalse(result.rules)

    def test_model_failure_is_atomic_and_not_silently_regex_fallback(self):
        def fail(text):
            raise RuntimeError('synthetic-failure-marker')
        detector = AdapterDetector(DetectorSettings(mode='gliner', model_dir=Path('unused-test-model')),
                                   predictor=fail)
        client, state = self.client(detector, FixedLinker())
        payload = dict(session_id=state['session_id'], session_epoch=state['session_epoch'],
            expected_state_version=0, message=dict(message_id=1, text='synthetic-failure-marker'))
        response = client.post('/v1/analyze', json=payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('synthetic-failure-marker', response.text)
        self.assertEqual(client.get('/v1/sessions/adapter-synthetic').json()['state_version'], 0)

    def test_model_mode_requires_explicit_local_weights(self):
        with self.assertRaises(ValueError):
            DetectorSettings(mode='gliner')
        with self.assertRaises(ValueError):
            DetectorSettings(mode='unexpected')

    def test_corpus_replay_does_not_supply_gold_owners_to_service(self):
        from tools.replay_service_corpus import replay
        result = replay([dict(session_id='synthetic-replay',
            entities=[dict(type='phone', person_id=['gold-owner-a', 'gold-owner-b'])],
            messages=[dict(id='m1', text='Мой телефон +7 (000) 000-00-01.'),
                      dict(id='m2', text='Моя почта synthetic@example.invalid.')])])
        self.assertFalse(result['gold_supplied'])
        self.assertFalse(result['quality_metrics_computed'])
        verdict = result['sessions'][0]['verdict']
        self.assertEqual(verdict['person_risks'], {})
        self.assertEqual(verdict['unknown_risk']['risk_score'], .6)
        self.assertEqual(verdict['first_alert_reason'], 'score_rule')
        self.assertFalse(verdict['coverage_complete'])


if __name__ == '__main__':
    unittest.main()
