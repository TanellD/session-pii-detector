"""Структура авторского корпуса и независимый порядок раскрытия атрибутов."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_complex_200 import build


class Complex200Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary_root = ROOT / 'data/tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        cls.output = Path(cls.temp.name) / 'corpus'
        cls.manifest = build(cls.output)
        cls.source = json.loads((cls.output / 'snapshot.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_lengths_people_and_synthetic_contacts(self):
        self.assertEqual(len(self.source['records']), 200)
        self.assertEqual(self.manifest['people'], {2: 100, 3: 100})
        for record in self.source['records']:
            doc = record['gold']
            self.assertTrue(5 <= len(doc['messages']) <= 10)
            messages = {m['id']: m['text'] for m in doc['messages']}
            owners = set()
            for entity in doc['entities']:
                owner = entity['person_id']
                owners.update(owner if isinstance(owner, list) else [owner] if owner else [])
                value = messages[entity['message_id']][entity['start']:entity['end']]
                if entity['type'] == 'phone':
                    self.assertIn('(000)', value)
                if entity['type'] == 'email':
                    self.assertTrue(value.endswith('@example.invalid'))
            self.assertEqual(len(owners), record['people'])

    def test_shared_number_can_trigger_before_last_contact(self):
        for record in self.source['records']:
            if record['family'] != 'repair_shared':
                continue
            doc = record['gold']
            shared = next(e for e in doc['entities'] if isinstance(e['person_id'], list))
            self.assertEqual(record['expected_first_alert'], int(shared['message_id'][1:]))
            self.assertEqual(set(shared['person_id']), {'a', 'b'})

    def test_no_future_reassignment_and_prefix_alerts(self):
        for record in self.source['records']:
            first = record['expected_first_alert']
            for i, row in enumerate(record['author_prefix_ledger'], 1):
                self.assertEqual(row['expected_first_alert'], first if first and i >= first else None)
            if record['family'] == 'late_contact_owner':
                phones = [e for e in record['gold']['entities'] if e['type'] == 'phone']
                self.assertIsNone(phones[0]['person_id'])
                self.assertEqual(phones[1]['person_id'], 'b')
        self.assertFalse(self.manifest['service_weights_used_for_gold'])

    def test_sessions_are_unique_and_all_families_present(self):
        records = self.source['records']
        self.assertEqual(len({r['session_id'] for r in records}), 200)
        self.assertEqual(len({tuple(m['text'] for m in r['gold']['messages']) for r in records}), 200)
        self.assertEqual(len(self.manifest['families']), 20)
        self.assertEqual(self.manifest['positive_sessions'], 120)

    def test_live_report_shows_preparation_without_old_corpus(self):
        from watch_gemma_linker import render
        run = Path(self.temp.name) / 'run'
        (run / 'logs').mkdir(parents=True)
        (run / 'worker.pid').write_text('123', encoding='utf-8')
        (run / 'preparation.json').write_text(json.dumps(dict(
            total_sessions=200, total_messages=1420, unique_texts=50)), encoding='utf-8')
        (run / 'logs/worker-stdout.log').write_text('Inference completed: 25 unique texts\n', encoding='utf-8')
        with patch('watch_gemma_linker.alive', return_value=True):
            self.assertFalse(render(run))
        text = (run / 'live-progress.md').read_text(encoding='utf-8')
        self.assertIn('25/50', text)
        self.assertIn('1420', text)
        self.assertNotIn('1000', text)


if __name__ == '__main__':
    unittest.main()
