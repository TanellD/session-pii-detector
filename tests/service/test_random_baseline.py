"""Проверки распределения исходов и независимых счётчиков оценщика."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'),str(ROOT)]
from session_pii.random_linker import RandomLinker
from session_pii.components import Fact, DetectionResult, EmptyDetector
from session_pii.models import Candidate, Message, Span
from session_pii.detectors import DetectorSettings
from tools.evaluate_random_linker import alerts, extraction, pairwise, session_seed, record_detector, run_pipeline, http_replay


class FixedRng:
    def __init__(self, choice, expected):
        self.choice, self.expected = choice, expected

    def randrange(self, n):
        assert n == self.expected
        return self.choice


class RandomBaselineTests(unittest.TestCase):
    def test_n_plus_two_distinct_equiprobable_choices(self):
        facts = (Fact('email','a@example.invalid',('a','b'),'e1',1),)
        candidate = Candidate(type='phone',start=0,end=1)
        results = []
        for choice in range(4):
            linker = RandomLinker()
            linker.rng = FixedRng(choice,4)
            results.append(linker.link(Message(message_id=2,text='x'),(candidate,),facts).person_ids[0])
        self.assertEqual(results,['a','b','random-1',None])

    def test_new_people_available_in_same_message_and_replay(self):
        candidate = Candidate(type='first_name',start=0,end=1)
        linker = RandomLinker()
        class Rng:
            def __init__(self): self.n=[]
            def randrange(self,n):
                self.n.append(n)
                return 0
        linker.rng=Rng()
        result=linker.link(Message(message_id=1,text='x'),(candidate,candidate),())
        self.assertEqual(result.person_ids,('random-1','random-1'))
        self.assertEqual(linker.rng.n,[2,3])
        args=(Message(message_id=1,text='x'),(candidate,)*20,())
        self.assertEqual(RandomLinker(42).link(*args),RandomLinker(42).link(*args))
        self.assertNotEqual(session_seed(0,'s1'),session_seed(0,'s2'))

    def test_extraction_penalizes_duplicates(self):
        e=dict(message_id='m1',start=0,end=1,type='phone')
        self.assertEqual(extraction([{'entities':[e]}],[[e,e]])['micro'],
                         dict(tp=1,fp=1,fn=0,precision=.5,recall=1.,f2=5/6))

    def test_unknown_is_not_shared_owner_and_joint_owners_work(self):
        gold={'entities':[dict(person_id=['a','b']),dict(person_id='b'),dict(person_id=None)]}
        result=pairwise([gold],[[dict(person_id=None),dict(person_id=None),dict(person_id='x')]])
        self.assertEqual(result['counts'],{'fn':1})
        self.assertEqual(result['excluded_gold_unknown_mentions'],1)
        self.assertEqual(pairwise([gold],[gold['entities']])['f1'],1.)

    def test_early_timely_is_only_fp(self):
        result=alerts([2,2,None],[1,3,None])
        self.assertEqual(result['session']['tp'],2)
        self.assertEqual(result['timely']['tp'],0)
        self.assertEqual(result['timely']['fp'],1)
        self.assertEqual(result['timely']['fn'],1)
        self.assertEqual(result['offsets'],[-1,1])

    def test_recorded_detector_uses_only_text_and_reuses_real_output(self):
        calls=[]
        class Detector:
            def detect(self,message):
                calls.append(message.text)
                return DetectionResult((Span(type='person',start=0,end=1),))
        # Нет entities/person_id/family: записи детектора не могут читать gold.
        docs=[dict(messages=[dict(id='m1',text='x'),dict(id='m2',text='x')]),
              dict(messages=[dict(id='m1',text='y')])]
        with tempfile.TemporaryDirectory() as directory:
            with patch('tools.evaluate_random_linker.build_detectors',return_value=(Detector(),EmptyDetector())):
                detector,records=record_detector(docs,DetectorSettings(mode='regex'),Path(directory))
            self.assertEqual(calls,['x','y'])
            self.assertEqual(len(records),2)
            self.assertEqual(detector.detect(Message(message_id=19,text='x')).spans[0].type,'person')
            self.assertEqual(calls,['x','y'])
            reused,_=record_detector(docs,DetectorSettings(mode='regex'),Path(directory),reuse=True)
            self.assertEqual(reused.detect(Message(message_id=2,text='y')).spans,detector.detect(Message(message_id=1,text='y')).spans)
            with self.assertRaisesRegex(ValueError,'другому'):
                record_detector([dict(messages=[dict(id='m1',text='z')])],DetectorSettings(mode='regex'),Path(directory),reuse=True)

    def test_invalid_model_email_is_runtime_failure_in_pipeline_and_http(self):
        class Invalid:
            def detect(self,message):
                return DetectionResult((Span(type='email',start=0,end=3),))
        doc=dict(session_id='synthetic-failure',messages=[dict(id='m1',text='abc')])
        result=run_pipeline(doc,'unused',0,DetectorSettings(mode='regex'),recorded=Invalid())
        self.assertEqual(result['runtime_error']['message_index'],1)
        self.assertFalse(result['coverage_complete'])
        self.assertIn('runtime_contract_error',result['incomplete_reasons'])
        self.assertIsNone(result['first_alert'])
        self.assertEqual(result['entities'],[])
        http=http_replay([doc],DetectorSettings(mode='regex'),0,recorded=Invalid())[0]
        self.assertEqual(http['runtime_error'],dict(message_index=1,http_status=502))
        self.assertEqual(http['entities'],[])


if __name__=='__main__':
    unittest.main()
