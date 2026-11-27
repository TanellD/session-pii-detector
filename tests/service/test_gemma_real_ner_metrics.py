"""Оценка линковки на пересечении спанов при реальных ошибках NER."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src'),str(ROOT)]
from evaluate_gemma_linker import matched_linker_metrics, expected_alert


class MatchedLinkerMetricsTests(unittest.TestCase):
    def test_authored_alert_and_negative_are_preserved(self):
        record=dict(gold=dict(messages=[{}, {}, {}]),author_expected_first_alert=['m3','combination_rule'],expected_first_alert=3)
        self.assertEqual(expected_alert(record,None),dict(message_index=3))
        record['expected_first_alert']=None
        self.assertIsNone(expected_alert(record,None))
        record['expected_first_alert']=4
        with self.assertRaises(ValueError):
            expected_alert(record,None)

    def test_missing_extra_and_wrong_type_are_not_aligned_by_position(self):
        def e(start,kind='first_name',owner='gold-person'):
            return dict(message_id='m1',start=start,end=start+1,type=kind,person_id=owner)
        doc=dict(entities=[e(0),e(2),e(4)])
        pred=dict(entities=[e(9,owner='other'),e(2,owner='pred-person'),
                            e(0,owner='pred-person'),e(4,'email',owner='other')])
        result=matched_linker_metrics([doc],[pred])
        self.assertEqual(result['matched_mentions'],2)
        self.assertEqual(result['gold_mentions_in_completed_sessions'],3)
        self.assertEqual(result['counts']['tp'],1)
        self.assertEqual(result['counts'].get('fp',0),0)
        self.assertEqual(result['recall'],1)

    def test_empty_matches_give_undefined_metrics(self):
        result=matched_linker_metrics([dict(entities=[])],[dict(entities=[])])
        self.assertIsNone(result['f1'])
        self.assertEqual(result['matched_mentions'],0)


if __name__=='__main__':
    unittest.main()
