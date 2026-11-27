"""Контракт сменного линкера, отсутствие gold во входе и восстановление записи."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[1]/'src')]
from run_linker_benchmark import FrozenDetector, gold_input, write
from cheap_linker_baselines import all_unknown, one_person
from session_pii.components import EmptyDetector
from session_pii.gemma_linker import GemmaLinker, GemmaSettings
from session_pii.models import Message, Verdict
from session_pii.pipeline import Pipeline


class LinkerBenchmarkTests(unittest.TestCase):
    def test_gold_candidates_exclude_owners_and_labels(self):
        doc = dict(session_id='synthetic-unit', messages=[dict(id='m1', text='Макетимир')],
                   entities=[dict(message_id='m1', start=0, end=9, type='first_name', person_id='hidden-owner')])
        value = gold_input(doc)
        self.assertNotIn('hidden-owner', json.dumps(value))
        self.assertEqual(FrozenDetector(value).detect(Message(message_id=1, text='Макетимир')).spans[0].type, 'first_name')

    def test_runtime_request_uses_own_previous_owner(self):
        requests = []
        def complete(body):
            request = json.loads(body['messages'][-1]['content'])
            requests.append(request)
            return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(one_person(request))))])
        linker = GemmaLinker(GemmaSettings(api_key='unused', base_url='http://localhost'), complete=complete)
        detector = FrozenDetector(dict(messages=[dict(text='Макетимир', complete=True, reasons=[],
            spans=[dict(type='first_name', start=0, end=9)]), dict(text='Пробов', complete=True, reasons=[],
            spans=[dict(type='last_name', start=0, end=6)])]))
        pipeline = Pipeline(detector, EmptyDetector(), linker)
        first_message = Message(message_id=1, text='Макетимир')
        first = pipeline.process(first_message, (), Verdict())
        second = pipeline.process(Message(message_id=2, text='Пробов'), first.facts, first.verdict,
                                  previous_messages=(first_message,))
        self.assertEqual(first.entities[0].person_id, second.entities[0].person_id)
        self.assertEqual(requests[0]['known_people'], [])
        self.assertEqual(requests[1]['known_people'][0]['person_id'], first.entities[0].person_id)
        self.assertEqual(len(requests[1]['previous_messages']), 1)
        self.assertNotIn('expected_first_alert', requests[1])
        self.assertEqual(all_unknown(requests[1])['assignments'][0]['owners'], [])

    def test_transient_windows_file_lock_is_retried(self):
        original = Path.replace
        count = 0
        def locked_once(path, target):
            nonlocal count
            count += 1
            if count == 1:
                raise PermissionError('synthetic lock')
            return original(path, target)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'status.json'
            with patch.object(Path, 'replace', locked_once), patch('run_linker_benchmark.time.sleep'):
                write(target, {'complete': True})
            self.assertEqual(json.loads(target.read_text()), {'complete': True})
            self.assertEqual(count, 2)


if __name__ == '__main__':
    unittest.main()
