"""Контракт владельцев, изоляция сессий и атомарность без внешнего API."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from fastapi.testclient import TestClient
from session_pii.app import create_app
from session_pii.components import DetectionResult, EmptyDetector, Fact
from session_pii.gemma_linker import GemmaLinker, GemmaSettings, build_linker
from session_pii.models import Candidate, Message, Span


def completion(rows,finish='stop'):
    return dict(choices=[dict(finish_reason=finish,message=dict(content=json.dumps(dict(assignments=rows))))])


def row(candidate,owners,service=False):
    return dict(candidate_id=candidate,owners=owners,service_contact=service)


class GemmaLinkerTests(unittest.TestCase):
    def settings(self): return GemmaSettings(api_key='synthetic-test-key')

    def test_alternative_model_config_keeps_default_and_secret_private(self):
        config=ROOT/'configs/linker-minicpm-baseline.json'
        with patch.dict(os.environ, {'RADEON_API_KEY':'synthetic-test-key','PII_LINKER_CONFIG':str(config)}):
            selected=GemmaSettings.from_env()
            self.assertEqual(selected.model,'MiniCPM5-2B')
            self.assertEqual(selected.context_messages,3)
            body=GemmaLinker(selected).request(Message(message_id=1,text='Тестимир'),
                (Candidate(type='first_name',start=0,end=8),),())
            self.assertEqual(body['reasoning_effort'],'low')
            default=GemmaLinker(self.settings()).request(Message(message_id=1,text='Тестимир'),
                (Candidate(type='first_name',start=0,end=8),),())
            self.assertNotIn('reasoning_effort',default)
            self.assertNotIn('synthetic-test-key',repr(selected))
            self.assertEqual(GemmaSettings.from_env(ROOT/'configs/linker-gemma-baseline.json').model,'Gemma-4-31B-IT')

    def test_new_shared_existing_and_unknown(self):
        text='Тестимир Макетослав a@example.invalid'
        candidates=(Candidate(type='first_name',start=0,end=8),Candidate(type='first_name',start=9,end=19),
                    Candidate(type='email',start=20,end=len(text)))
        linker=GemmaLinker(self.settings(),complete=lambda body:completion([
            row('c3',['new:1','new:2']),row('c1',['new:1']),row('c2',['new:2'])]))
        result=linker.link(Message(message_id=1,text=text),candidates,())
        self.assertEqual(result.person_ids,('gemma-m1-p1','gemma-m1-p2',('gemma-m1-p1','gemma-m1-p2')))
        facts=(Fact('first_name','Тестимир',result.person_ids[0],'e1',1),)
        linker=GemmaLinker(self.settings(),complete=lambda body:completion([row('c1',['gemma-m1-p1'])]))
        self.assertEqual(linker.link(Message(message_id=2,text='x'),(candidates[0],),facts).person_ids,('gemma-m1-p1',))
        linker=GemmaLinker(self.settings(),complete=lambda body:completion([row('c1',[])]))
        self.assertEqual(linker.link(Message(message_id=2,text='x'),(candidates[0],),facts).person_ids,(None,))

    def test_invalid_model_output_rejected(self):
        candidate=(Candidate(type='first_name',start=0,end=8),)
        for rows in [[],[row('c2',[])],[row('c1',[]),row('c1',[])],[row('c1',['invented-person'])],
                     [row('c1',['new:1','new:1'])],[row('c1',[],True)],
                     [{**row('c1',[]),'changed_type':'email'}]]:
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                GemmaLinker(self.settings(),complete=lambda body:completion(rows)).link(
                    Message(message_id=1,text='Тестимир'),candidate,())
        with self.assertRaises(ValueError):
            GemmaLinker(self.settings(),complete=lambda body:completion([row('c1',[])],finish='length')).link(
                Message(message_id=1,text='Тестимир'),candidate,())

    def test_service_contacts_and_no_request_for_empty_candidates(self):
        calls=[]
        linker=GemmaLinker(self.settings(),complete=lambda body:(calls.append(body) or completion([row('c1',[],True)])))
        self.assertEqual(linker.link(Message(message_id=1,text='x'),(),()).person_ids,())
        self.assertEqual(calls,[])
        result=linker.link(Message(message_id=1,text='a@example.invalid'),(Candidate(type='email',start=0,end=17),),())
        self.assertEqual(result.person_ids,(None,))
        self.assertEqual(result.service_contacts,(True,))
        self.assertNotIn('synthetic-test-key',json.dumps(calls))
        self.assertNotIn('synthetic-test-key',repr(self.settings()))

    def test_prompt_has_only_current_candidates_and_predicted_facts(self):
        facts=(Fact('email','a@example.invalid',('p1','p2'),'e1',1),Fact('phone','+70000000001',None,'e2',1))
        linker=GemmaLinker(self.settings(),complete=lambda body:None)
        body=linker.request(Message(message_id=2,text='Моя почта'),(Candidate(type='email',start=0,end=9),),facts)
        data=json.loads(body['messages'][1]['content'])
        self.assertEqual([p['person_id'] for p in data['known_people']],['p1','p2'])
        self.assertEqual(len(data['unowned_facts']),1)
        self.assertEqual(set(data),{'message','previous_messages','candidates','known_people','unowned_facts'})
        self.assertNotIn('risk',json.dumps(data))

    def test_context_limit_is_explicit(self):
        linker=GemmaLinker(GemmaSettings(api_key='x',max_context_facts=1),complete=lambda body:None)
        with self.assertRaisesRegex(ValueError,'context limit'):
            linker.link(Message(message_id=2,text='x'),(Candidate(type='person',start=0,end=1),),
                        (Fact('person','x','p','e1',1),Fact('person','y','q','e2',1)))

    def test_http_retry_reset_and_separate_sessions(self):
        requests=[]
        def respond(body):
            data=json.loads(body['messages'][1]['content'])
            requests.append(data)
            owners=[data['known_people'][0]['person_id']] if data['known_people'] else ['new:1']
            return completion([row('c1',owners)])
        class Detector:
            def detect(self,message):
                return DetectionResult((Span(type='first_name' if message.message_id==1 else 'email',start=0,end=len(message.text)),))
        client=TestClient(create_app(ner=Detector(),regexp=EmptyDetector(),linker=GemmaLinker(self.settings(),complete=respond)))
        state=client.post('/v1/sessions',json={'session_id':'synthetic-a'}).json()
        def payload(state,text):
            return dict(session_id=state['session_id'],session_epoch=state['session_epoch'],
                expected_state_version=state['state_version'],message=dict(message_id=state['next_message_id'],text=text))
        first=payload(state,'Тестимир')
        response=client.post('/v1/analyze',json=first)
        self.assertEqual(response.status_code,200)
        state=response.json()
        self.assertEqual(client.post('/v1/analyze',json=first).json(),state)
        self.assertEqual(len(requests),1)
        second=client.post('/v1/analyze',json=payload(state,'a@example.invalid'))
        self.assertEqual(second.status_code,200)
        self.assertEqual(requests[1]['previous_messages'],[dict(message_id=1,text='Тестимир')])
        self.assertEqual(second.json()['entities'][0]['person_id'],'gemma-m1-p1')
        other=client.post('/v1/sessions',json={'session_id':'synthetic-b'}).json()
        self.assertEqual(client.post('/v1/analyze',json=payload(other,'Макетослав')).status_code,200)
        self.assertEqual(requests[-1]['known_people'],[])
        self.assertEqual(requests[-1]['previous_messages'],[])
        state=second.json()
        state=client.post('/v1/sessions/synthetic-a/reset',json=dict(session_epoch=state['session_epoch'],
                          expected_state_version=state['state_version'])).json()
        self.assertEqual(client.post('/v1/analyze',json=payload(state,'Тестимир')).status_code,200)
        self.assertEqual(requests[-1]['known_people'],[])
        self.assertEqual(requests[-1]['previous_messages'],[])

    def test_explicit_configuration(self):
        with patch.dict(os.environ,{'PII_LINKER_MODE':'stub'}):
            self.assertEqual(type(build_linker()).__name__,'StubLinker')
        with patch.dict(os.environ,{'PII_LINKER_MODE':'gemma','RADEON_API_KEY':'synthetic-test-key'}):
            self.assertIsInstance(build_linker(),GemmaLinker)

    def test_failed_assignment_does_not_commit_session_or_owner_state(self):
        calls=[]
        def respond(body):
            calls.append(json.loads(body['messages'][1]['content']))
            return completion([row('c1',['invented' if len(calls)==1 else 'new:1'])])
        class Detector:
            def detect(self,message):
                return DetectionResult((Span(type='first_name',start=0,end=len(message.text)),))
        client=TestClient(create_app(ner=Detector(),regexp=EmptyDetector(),linker=GemmaLinker(self.settings(),complete=respond)))
        state=client.post('/v1/sessions',json={'session_id':'synthetic-atomic'}).json()
        request=dict(session_id=state['session_id'],session_epoch=state['session_epoch'],expected_state_version=0,
                     message=dict(message_id=1,text='Тестимир'))
        self.assertEqual(client.post('/v1/analyze',json=request).status_code,502)
        self.assertEqual(client.get('/v1/sessions/synthetic-atomic').json()['state_version'],0)
        response=client.post('/v1/analyze',json=request)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['entities'][0]['person_id'],'gemma-m1-p1')
        self.assertEqual(calls[0],calls[1])

    def test_context_window_includes_messages_without_candidates_and_is_not_exposed(self):
        calls=[]
        def respond(body):
            calls.append(json.loads(body['messages'][1]['content']))
            return completion([row('c1',['new:1'] if len(calls)==1 else ['gemma-m1-p1'])])
        class Detector:
            def detect(self,message):
                if message.text.startswith('Без фактов'):
                    return DetectionResult()
                return DetectionResult((Span(type='first_name' if message.message_id==1 else 'email',start=0,end=len(message.text)),))
        app=create_app(ner=Detector(),regexp=EmptyDetector(),linker=GemmaLinker(self.settings(),complete=respond))
        client=TestClient(app)
        state=client.post('/v1/sessions',json={'session_id':'synthetic-context'}).json()
        for text in ['Тестимир','Без фактов 1','Без фактов 2','Без фактов 3','a@example.invalid']:
            response=client.post('/v1/analyze',json=dict(session_id=state['session_id'],session_epoch=state['session_epoch'],
                expected_state_version=state['state_version'],message=dict(message_id=state['next_message_id'],text=text)))
            self.assertEqual(response.status_code,200)
            state=response.json()
        self.assertEqual(len(calls),2)
        self.assertEqual([m['message_id'] for m in calls[1]['previous_messages']],[2,3,4])
        self.assertEqual(len(app.state.service.sessions['synthetic-context'].link_context),3)
        self.assertNotIn('Без фактов',client.get('/v1/sessions/synthetic-context').text)

    def test_future_context_rejected_and_zero_window_supported(self):
        linker=GemmaLinker(self.settings(),complete=lambda body:None)
        with self.assertRaisesRegex(ValueError,'future'):
            linker.link_with_context(Message(message_id=2,text='x'),(Candidate(type='person',start=0,end=1),),(),
                                     (Message(message_id=2,text='future'),))
        linker=GemmaLinker(GemmaSettings(api_key='x',context_messages=0),complete=lambda body:None)
        data=json.loads(linker.request(Message(message_id=2,text='x'),(Candidate(type='person',start=0,end=1),),(),
            (Message(message_id=1,text='past'),))['messages'][1]['content'])
        self.assertEqual(data['previous_messages'],[])


if __name__=='__main__':
    unittest.main()
