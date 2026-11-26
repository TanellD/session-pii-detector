"""Гарантии обучающих примеров: общий новый владелец, null и префиксы."""
import sys
from pathlib import Path
import random
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from build_session_benchmark import linker_rows, layout


class BenchmarkExportsTests(unittest.TestCase):
    def test_linker_targets_share_new_person_and_never_use_future(self):
        def entity(eid, mid, start, end, owners):
            return dict(id=eid, message_id=mid, start=start, end=end, type='phone', person_id=owners)
        record = dict(gold_service_contacts=[], gold=dict(session_id='synthetic-unit',
            messages=[dict(id='m1', text='11 22'), dict(id='m2', text='33'), dict(id='m3', text='44')],
            entities=[entity('e1', 'm1', 0, 2, ['a', 'b']), entity('e2', 'm1', 3, 5, 'a'),
                      entity('e3', 'm2', 0, 2, None), entity('e4', 'm3', 0, 2, 'c')]))
        rows = list(linker_rows([record]))
        self.assertEqual(rows[0]['request']['known_people'], [])
        self.assertEqual(rows[0]['target']['assignments'][0]['owners'], ['new:1', 'new:2'])
        self.assertEqual(rows[0]['target']['assignments'][1]['owners'], ['new:1'])
        self.assertEqual({p['person_id'] for p in rows[1]['request']['known_people']}, {'a', 'b'})
        self.assertEqual(rows[1]['target']['assignments'][0]['owners'], [])
        self.assertEqual(len(rows[2]['request']['unowned_facts']), 1)
        self.assertEqual(len(rows[1]['request']['previous_messages']), 1)
        self.assertNotIn('44', str(rows[1]['request']))

    def test_layout_preserves_fact_order_and_message_limits(self):
        for target in (2, 5, 6, 8):
            result = layout(['FACT_A', 'FACT_B', 'FACT_C'], target, 'train', random.Random(32))
            self.assertEqual(len(result), target)
            text = ' '.join(result)
            self.assertLess(text.index('FACT_A'), text.index('FACT_B'))
            self.assertLess(text.index('FACT_B'), text.index('FACT_C'))


if __name__ == '__main__':
    unittest.main()
