"""Временные сбои API не теряют успешную разметку и не превращаются в отрицательные метки."""
from io import BytesIO
from http.client import RemoteDisconnected
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from prepare_annotation_recovery import build
from run_sequential_annotation import send_with_retries
from seal_interrupted_annotation import seal
from watch_corpus_comparison import restore_watch_state


class AnnotationRecoveryTests(unittest.TestCase):
    def test_interruption_backs_up_manifest_without_altering_valid_results(self):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp') as directory:
            root=Path(directory)
            run=root/'run'
            run.mkdir()
            old=dict(records=[dict(session_id='complete',status='valid'),dict(session_id='partial',status='requested')],
                     steps=[dict(session_id='complete',status='valid'),dict(session_id='partial',status='requested')])
            original=json.dumps(old).encode()
            (run/'manifest.json').write_bytes(original)
            (run/'response.json').write_bytes(b'synthetic-response-preserved')
            (root/'batch-manifest.json').write_text(json.dumps(dict(jobs=[dict(run=str(run),status='running')])),encoding='utf-8')
            self.assertEqual(seal(root)['status'],'sealed_interrupted')
            current=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(current['records'][0],old['records'][0])
            self.assertEqual(current['records'][1]['error_type'],'InterruptedForRecovery')
            self.assertEqual(next(run.glob('manifest-before-interruption-*')).read_bytes(),original)
            self.assertEqual((run/'response.json').read_bytes(),b'synthetic-response-preserved')
            self.assertEqual(seal(root)['status'],'already_finished')

    def test_watcher_restart_preserves_previous_runs_and_recovery_count(self):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp') as directory:
            root=Path(directory)
            first=root/'first'
            second=root/'second'
            resumed=root/'resumed'
            (root/'watch-state.json').write_text(json.dumps(dict(first_runs=[str(first)],second_runs=[str(second)],recoveries={'second':1})),encoding='utf-8')
            a,b,counts=restore_watch_state([first,resumed],[second],root)
            self.assertEqual(a,[first.resolve(),resumed.resolve()])
            self.assertEqual(b,[second.resolve()])
            self.assertEqual(counts['second'],1)

    def test_remote_disconnect_is_retryable_and_does_not_crash_the_batch(self):
        attempts=[]
        with patch('run_sequential_annotation.send',side_effect=[RemoteDisconnected('synthetic'),{'choices':[]}]),patch('run_sequential_annotation.time.sleep'):
            result=send_with_retries('https://example.invalid','synthetic-key',{},300,1,60,attempts,lambda:None)
        self.assertEqual(result,{'choices':[]})
        self.assertEqual(attempts[0]['error_type'],'RemoteDisconnected')
        self.assertEqual(attempts[1]['status'],'response')

    def test_rate_limit_retries_same_body_and_keeps_attempts(self):
        error=HTTPError('https://example.invalid',429,'rate limit',{},BytesIO(b''))
        attempts=[]
        callbacks=[]
        body={'messages':[{'role':'user','content':'synthetic'}]}
        with patch('run_sequential_annotation.send',side_effect=[error,{'choices':[]}]) as sender,patch('run_sequential_annotation.time.sleep') as sleep:
            result=send_with_retries('https://example.invalid','synthetic-key',body,120,3,60,attempts,lambda:callbacks.append(True))
        self.assertEqual(result,{'choices':[]})
        self.assertEqual(sender.call_count,2)
        self.assertEqual(sender.call_args_list[0],sender.call_args_list[1])
        self.assertEqual([a['status'] for a in attempts],['failed','response'])
        self.assertEqual(attempts[0]['http_status'],429)
        self.assertEqual(callbacks,[True])
        sleep.assert_called_once_with(60)

    def test_permanent_auth_error_is_not_retried(self):
        error=HTTPError('https://example.invalid',401,'unauthorized',{},BytesIO(b''))
        with patch('run_sequential_annotation.send',side_effect=error) as sender,patch('run_sequential_annotation.time.sleep') as sleep:
            with self.assertRaises(HTTPError):
                send_with_retries('https://example.invalid','synthetic-key',{},120,3,60,[],lambda:None)
        self.assertEqual(sender.call_count,1)
        sleep.assert_not_called()

    def test_timeout_retries_are_bounded(self):
        attempts=[]
        with patch('run_sequential_annotation.send',side_effect=TimeoutError) as sender,patch('run_sequential_annotation.time.sleep'):
            with self.assertRaises(TimeoutError):
                send_with_retries('https://example.invalid','synthetic-key',{},120,2,60,attempts,lambda:None)
        self.assertEqual(sender.call_count,3)
        self.assertEqual(len(attempts),3)

    def test_recovery_excludes_only_complete_sessions_and_preserves_previous_files(self):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp') as folder:
            parent=Path(folder)
            source=parent/'source'
            source.mkdir()
            previous=parent/'previous'
            previous.mkdir()
            run=previous/'run'
            run.mkdir()
            sessions=[dict(session_id=s,messages=[dict(id='m1',text='Нейтральный вопрос.')]) for s in ['a','b','c']]
            gold=[dict(**s,schema_version='annotation-mvp-v2',entities=[],case_flags=dict(is_example=False,annotation_uncertain=False,clarification_reason=None)) for s in sessions]
            objects={'sessions.json':dict(synthetic_only=True,sessions=sessions),'gold.json':dict(annotations=gold),
                     'gold-assessments.json':dict(checks=[dict(session_id=s['session_id'],assessment={}) for s in sessions]),
                     'gold-prefixes.json':dict(steps=[]),'manifest.json':dict(version='synthetic',message_selection='user_only',sessions=3)}
            for name,value in objects.items():
                (source/name).write_text(json.dumps(value),encoding='utf-8')
            (previous/'batch-manifest.json').write_text(json.dumps(dict(finished_at='synthetic',jobs=[dict(run=str(run))])),encoding='utf-8')
            prior=json.dumps(dict(records=[dict(session_id='a',status='valid'),dict(session_id='b',status='invalid_or_failed',completed_messages=0)]))
            (run/'manifest.json').write_text(prior,encoding='utf-8')
            result=build(source,[previous],parent/'recovery')
            self.assertEqual(result['skipped_successful_sessions'],1)
            self.assertEqual(result['remaining_sessions'],2)
            remaining=json.loads((parent/'recovery/source/sessions.json').read_text(encoding='utf-8'))
            self.assertEqual({s['session_id'] for s in remaining['sessions']},{'b','c'})
            self.assertEqual((run/'manifest.json').read_text(encoding='utf-8'),prior)


if __name__=='__main__':
    unittest.main()
