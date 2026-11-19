import copy
import contextlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from run_sequential_annotation import step_payload, merge_step, main


class SequentialAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.session = {'session_id':'synthetic','messages':[
            {'id':'m1','text':'Почта из заметки: note@example.invalid.'},
            {'id':'m2','text':'Я Тестимир.'},
            {'id':'m3','text':'FUTURE_ONLY_TEXT'}]}
        self.previous = {'session_id':'synthetic','schema_version':'annotation-mvp-v2',
            'messages':copy.deepcopy(self.session['messages'][:1]),
            'entities':[dict(id='e1',message_id='m1',start=17,end=37,type='email',person_id=None)],
            'case_flags':{'is_example':False,'annotation_uncertain':True,'clarification_reason':'specification_gap'}}
        start = self.session['messages'][0]['text'].index('note@example.invalid')
        self.previous['entities'][0].update(start=start,end=start+len('note@example.invalid'))

    def raw(self,payload):
        return {**payload['session'],'schema_version':'annotation-quotes-v1',
            'case_flags':{'is_example':False,'annotation_uncertain':False,'clarification_reason':None},
            'entities':[dict(id='e1',message_id='m2',mention='Тестимир',occurrence=1,type='first_name',person_id='p1')]}

    def test_payload_has_no_future_or_gold_and_is_detached(self):
        payload = step_payload(self.session,2,self.previous)
        self.assertNotIn('FUTURE_ONLY_TEXT',str(payload))
        self.assertEqual(payload['previous_annotation'],self.previous)
        payload['previous_annotation']['entities'][0]['person_id']='changed'
        self.assertIsNone(self.previous['entities'][0]['person_id'])

    def test_old_unknown_owner_and_flags_are_frozen(self):
        payload = step_payload(self.session,2,self.previous)
        before = copy.deepcopy(self.previous)
        document,result = merge_step(self.raw(payload),payload)
        self.assertEqual(document['entities'][0],before['entities'][0])
        self.assertIsNone(document['entities'][0]['person_id'])
        self.assertEqual(document['entities'][1]['id'],'e2')
        self.assertTrue(document['case_flags']['annotation_uncertain'])
        self.assertEqual(document['case_flags']['clarification_reason'],'specification_gap')
        self.assertEqual(self.previous,before)

    def test_returning_old_mentions_is_rejected(self):
        payload = step_payload(self.session,2,self.previous)
        raw = self.raw(payload)
        raw['entities'][0].update(message_id='m1',mention='note@example.invalid',type='email',person_id='p1')
        with self.assertRaisesRegex(ValueError,'прежнее сообщение'):
            merge_step(raw,payload)

    def test_future_or_changed_messages_are_rejected(self):
        payload = step_payload(self.session,2,self.previous)
        raw = self.raw(payload)
        raw['messages'] = copy.deepcopy(self.session['messages'])
        with self.assertRaises(ValueError):
            merge_step(raw,payload)
        with self.assertRaises(ValueError):
            step_payload(self.session,3,self.previous)

    def run_failure_case(self, first_error):
        root = Path(__file__).resolve().parents[1]
        (root/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root/'data/tmp') as directory:
            path = Path(directory)
            source = {'synthetic_only':True,'sessions':[
                {'session_id':'s1','messages':[{'id':'m1','text':'Начало.'},{'id':'m2','text':'Не отправлять после ошибки.'}]},
                {'session_id':'s2','messages':[{'id':'m1','text':'Другой вопрос.'}]}]}
            (path/'sessions.json').write_text(json.dumps(source),encoding='utf-8')
            (path/'manifest.json').write_text(json.dumps({'message_selection':'user_only'}),encoding='utf-8')
            calls = []
            def request(url,key,body,timeout):
                payload = json.loads(body['messages'][1]['content'])
                calls.append(payload)
                if len(calls)==1:
                    raise first_error
                doc = {**payload['session'],'schema_version':'annotation-quotes-v1','entities':[],
                       'case_flags':{'is_example':False,'annotation_uncertain':False,'clarification_reason':None}}
                return {'model':'Gemma-4-31B-IT','choices':[{'finish_reason':'stop','message':{'content':json.dumps(doc)}}]}
            with patch('run_sequential_annotation.local_key',return_value='synthetic-key'), \
                    patch('run_sequential_annotation.send',side_effect=request), \
                    patch('sys.argv',['run_sequential_annotation.py','--input',str(path/'sessions.json'),'--output',str(path/'run'),'--delay','0']), \
                    contextlib.redirect_stdout(StringIO()):
                self.assertEqual(main(),1)
            records = json.loads((path/'run/manifest.json').read_text(encoding='utf-8'))['records']
            return calls,records

    def test_timeout_stops_session_and_next_session_has_no_previous_state(self):
        calls,records = self.run_failure_case(TimeoutError())
        self.assertEqual([c['session']['session_id'] for c in calls],['s1','s2'])
        self.assertIsNone(calls[1]['previous_annotation'])
        self.assertEqual(records[0]['completed_messages'],0)
        self.assertEqual(records[1]['status'],'valid')

    def test_http_error_stops_all_sessions(self):
        calls,records = self.run_failure_case(HTTPError('https://example.invalid',429,'',None,None))
        self.assertEqual(len(calls),1)
        self.assertEqual(records[0]['http_status'],429)
