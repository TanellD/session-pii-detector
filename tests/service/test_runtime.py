"""Интеграционные проверки сервиса; только синтетические сообщения."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import unittest
from typing import get_args

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'),str(ROOT)]

from fastapi.testclient import TestClient
from session_pii.app import create_app
from session_pii.detectors import DetectorSettings
from session_pii.components import DetectionResult, LinkResult
from session_pii.models import AnalyzeRequest, EntityType, Span
from session_pii.service import Settings


class Detector:
    def __init__(self, fragments=()):
        self.fragments = fragments
        self.calls = []

    def detect(self, message):
        self.calls.append(message.text)
        return DetectionResult(tuple(Span(type=kind,start=message.text.index(value),
                                          end=message.text.index(value)+len(value))
                                     for value,kind in self.fragments if value in message.text))


class Linker:
    def __init__(self, person='p1'):
        self.person = person

    def link(self, message, candidates, known_facts):
        return LinkResult(tuple(self.person for _ in candidates))


class RuntimeTests(unittest.TestCase):
    def test_types_match_annotation_config(self):
        classes = json.loads((ROOT/'configs/annotation-classes.json').read_text(encoding='utf-8'))['classes']
        self.assertEqual(set(get_args(EntityType)),set(classes))

    def setUp(self):
        self.ner = Detector((('Макетонов','last_name'),('Тестимир','first_name')))
        self.regexp = Detector((('testimir@example.invalid','email'),('+7 (000) 000-00-01','phone'),
                                ('000000000000','individual_inn')))
        self.app = create_app(ner=self.ner,regexp=self.regexp,linker=Linker())
        self.client = TestClient(self.app)
        self.state = self.client.post('/v1/sessions',json={'session_id':'synthetic'}).json()

    def payload(self, text, state=None):
        state = state or self.state
        return dict(session_id=state['session_id'],session_epoch=state['session_epoch'],
                    expected_state_version=state['state_version'],
                    message=dict(message_id=state['next_message_id'],role='user',text=text))

    def send(self, text):
        response = self.client.post('/v1/analyze',json=self.payload(text))
        self.assertEqual(response.status_code,200,response.text)
        self.state = response.json()
        return self.state

    def test_default_stubs_are_explicitly_incomplete(self):
        client = TestClient(create_app(detector_settings=DetectorSettings(mode="stub")))
        state = client.post('/v1/sessions',json={'session_id':'synthetic'}).json()
        result = client.post('/v1/analyze',json=self.payload('Моя почта testimir@example.invalid.',state)).json()
        self.assertFalse(result['verdict']['session_alert'])
        self.assertFalse(result['verdict']['coverage_complete'])
        self.assertFalse(result['verdict']['annotation_uncertain'])
        self.assertEqual(set(result['verdict']['incomplete_reasons']),
                         {'ner_not_configured','regexp_not_configured','linker_not_configured'})

    def test_cross_message_rule_and_history(self):
        first = self.send('Я Макетонов Тестимир.')
        self.assertFalse(first['verdict']['session_alert'])
        self.assertFalse(first['verdict']['annotation_uncertain'])
        self.assertEqual(first['verdict']['person_risks']['p1']['risk_score'],0.3)
        second = self.send('Моя личная почта testimir@example.invalid.')
        self.assertTrue(second['verdict']['session_alert'])
        self.assertEqual(second['verdict']['first_alert_message_id'],2)
        self.assertEqual(second['verdict']['first_alert_reason'],'combination_rule')
        self.assertFalse(second['verdict']['annotation_uncertain'])
        self.assertFalse(second['verdict']['annotation_uncertain_ever'])
        self.send('Спасибо.')
        self.assertEqual(len(self.ner.calls),3)
        self.assertEqual(self.ner.calls[1],'Моя личная почта testimir@example.invalid.')
        self.assertEqual(self.state['verdict']['first_alert_message_id'],2)
        history = self.client.get('/v1/sessions/synthetic').json()['message_history']
        self.assertEqual([m['annotation_uncertain'] for m in history],[False,False,False])

    def test_unknown_contacts_do_not_form_person(self):
        self.app.state.service.pipeline.linker = Linker(None)
        result = self.send('В записи телефон +7 (000) 000-00-01, почта testimir@example.invalid.')
        self.assertTrue(result['verdict']['session_alert'])
        self.assertEqual(result['verdict']['first_alert_reason'],'score_rule')
        self.assertEqual(result['verdict']['unknown_risk']['rules'],['score_rule'])
        self.assertTrue(result['verdict']['coverage_complete'])
        self.assertFalse(result['verdict']['annotation_uncertain'])
        self.assertTrue(all(e['person_id'] is None for e in result['entities']))

    def test_service_contacts_are_excluded_from_unknown_score(self):
        class ServiceLinker:
            def link(self, message, candidates, known_facts):
                return LinkResult(tuple(None for _ in candidates),
                                  service_contacts=tuple(True for _ in candidates))
        self.app.state.service.pipeline.linker = ServiceLinker()
        result = self.send('Общий телефон +7 (000) 000-00-01, почта testimir@example.invalid.')
        self.assertFalse(result['verdict']['session_alert'])
        self.assertEqual(result['verdict']['unknown_risk']['risk_score'],0.0)

    def test_unknown_owner_red_still_alerts(self):
        self.app.state.service.pipeline.linker = Linker(None)
        result = self.send('ИНН физического лица: 000000000000.')
        self.assertTrue(result['verdict']['session_alert'])
        self.assertIsNone(result['entities'][0]['person_id'])
        self.assertEqual(result['verdict']['first_alert_reason'],'red_rule')

    def test_proven_red_alert_survives_incomplete_components(self):
        from session_pii.components import StubLinker, StubNER
        self.app.state.service.pipeline.ner = StubNER()
        self.app.state.service.pipeline.linker = StubLinker()
        result = self.send('ИНН физического лица: 000000000000.')
        self.assertTrue(result['verdict']['session_alert'])
        self.assertFalse(result['verdict']['coverage_complete'])

    def test_offsets_are_unicode_codepoints(self):
        text = '🙂 Я Тестимир.'
        result = self.send(text)
        entity = result['entities'][0]
        self.assertEqual(text[entity['start']:entity['end']],'Тестимир')
        self.assertEqual(entity['start'],4)  # Emoji — один code point, два UTF-16 code units.

    def test_exact_retry_does_not_invoke_components(self):
        payload = self.payload('Я Тестимир.')
        first = self.client.post('/v1/analyze',json=payload)
        second = self.client.post('/v1/analyze',json=payload)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(len(self.ner.calls),1)
        payload['message']['text'] = 'Другой текст.'
        self.assertEqual(self.client.post('/v1/analyze',json=payload).status_code,409)

    def test_concurrent_exact_retries_commit_once(self):
        request = AnalyzeRequest.model_validate(self.payload('Я Тестимир.'))
        service = self.app.state.service
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(service.analyze,[request,request]))
        self.assertEqual(outputs[0],outputs[1])
        self.assertEqual(service.get('synthetic').state_version,1)
        self.assertEqual(len(self.ner.calls),1)

    def test_deduplication_and_overlap_are_not_double_counted(self):
        self.app.state.service.pipeline.regexp = Detector((('Тестимир','first_name'),))
        result = self.send('Я Тестимир.')
        self.assertEqual(len(result['entities']),1)
        self.assertEqual(result['entities'][0]['sources'],['ner','regexp'])
        # Другой тип на том же фрагменте: требуется отдельное разрешение конфликта.
        self.app.state.service.pipeline.regexp = Detector((('Тестимир','last_name'),))
        response = self.client.post('/v1/analyze',json=self.payload('Я Тестимир.'))
        self.assertEqual(response.status_code,502)
        self.assertEqual(self.client.get('/v1/sessions/synthetic').json()['state_version'],1)

    def test_invalid_offsets_and_linker_do_not_commit(self):
        class BadDetector:
            def detect(self, message):
                return DetectionResult((Span(type='first_name',start=0,end=999),))
        self.app.state.service.pipeline.ner = BadDetector()
        response = self.client.post('/v1/analyze',json=self.payload('Тестимир'))
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json()['state']['state_version'],0)
        class BadLinker:
            def link(self, message, candidates, known_facts):
                return LinkResult(())
        self.app.state.service.pipeline.ner = self.ner
        self.app.state.service.pipeline.linker = BadLinker()
        response = self.client.post('/v1/analyze',json=self.payload('Я Тестимир.'))
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json()['state']['state_version'],0)

    def test_failure_never_echoes_text_and_does_not_commit(self):
        class Failing:
            def detect(self, message):
                raise ValueError('RAW_SECRET_SENTINEL')
        self.app.state.service.pipeline.ner = Failing()
        response = self.client.post('/v1/analyze',json=self.payload('RAW_SECRET_SENTINEL'))
        self.assertEqual(response.status_code,502)
        self.assertNotIn('RAW_SECRET_SENTINEL',response.text)
        self.assertEqual(self.client.get('/v1/sessions/synthetic').json()['state_version'],0)
        bad = self.payload('RAW_SECRET_SENTINEL')
        bad['unexpected'] = 'RAW_SECRET_SENTINEL'
        response = self.client.post('/v1/analyze',json=bad)
        self.assertEqual(response.status_code,422)
        self.assertNotIn('RAW_SECRET_SENTINEL',response.text)

    def test_order_roles_and_reset_epoch(self):
        payload = self.payload('Спасибо.')
        payload['message']['message_id'] = 2
        self.assertEqual(self.client.post('/v1/analyze',json=payload).status_code,409)
        payload['message']['message_id'] = 1
        payload['message']['role'] = 'assistant'
        self.assertEqual(self.client.post('/v1/analyze',json=payload).status_code,422)
        self.send('Я Тестимир.')
        old_epoch = self.state['session_epoch']
        response = self.client.post('/v1/sessions/synthetic/reset',json={
            'session_epoch':old_epoch,'expected_state_version':1})
        self.assertEqual(response.status_code,200)
        self.assertNotEqual(response.json()['session_epoch'],old_epoch)
        self.assertEqual(response.json()['state_version'],0)
        self.assertEqual(self.client.post('/v1/analyze',json=self.payload('Старый epoch.')).status_code,409)

    def test_sessions_do_not_share_people(self):
        self.send('Я Макетонов Тестимир.')
        state = self.client.post('/v1/sessions',json={'session_id':'another'}).json()
        result = self.client.post('/v1/analyze',json=self.payload('Моя почта testimir@example.invalid.',state)).json()
        self.assertFalse(result['verdict']['session_alert'])

    def test_ttl_capacity_limits_and_no_get_extension(self):
        clock = [0]
        app = create_app(ner=self.ner,regexp=self.regexp,linker=Linker(),
                         settings=Settings(ttl_seconds=2,max_sessions=1,max_messages=1,max_entities=1))
        app.state.service.clock = lambda:clock[0]
        client = TestClient(app)
        state = client.post('/v1/sessions',json={'session_id':'synthetic'}).json()
        self.assertEqual(client.post('/v1/sessions',json={'session_id':'another'}).status_code,503)
        # Два спана нарушают лимит: нет частичного принятия сообщения.
        response = client.post('/v1/analyze',json=self.payload('Я Макетонов Тестимир.',state))
        self.assertEqual(response.status_code,413)
        self.assertEqual(response.json()['state']['state_version'],0)
        accepted = client.post('/v1/analyze',json=self.payload('Я Тестимир.',state)).json()
        self.assertEqual(client.post('/v1/analyze',json=self.payload('Спасибо.',accepted)).status_code,413)
        clock[0] = 1
        self.assertEqual(client.get('/v1/sessions/synthetic').status_code,200)
        clock[0] = 3
        self.assertEqual(client.get('/v1/sessions/synthetic').status_code,404)
        self.assertEqual(client.post('/v1/analyze',json=self.payload('Спасибо.',accepted)).status_code,410)
        recreated = client.post('/v1/sessions',json={'session_id':'synthetic'}).json()
        self.assertNotEqual(recreated['session_epoch'],state['session_epoch'])


if __name__=='__main__':
    unittest.main()
