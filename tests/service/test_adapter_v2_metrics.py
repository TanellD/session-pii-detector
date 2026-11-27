"""Сравниваемые варианты должны сохранять собственные предсказания."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'src'), str(ROOT)]
import evaluate_adapter_revision_v2 as evaluation
from session_pii.components import EmptyDetector


class AdapterV2MetricsTests(unittest.TestCase):
    def test_common_subset_does_not_reuse_last_variant_predictions(self):
        temporary_root = ROOT / 'data/tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            base = Path(directory)
            text = 'Я Пробов Макетослав.'
            entities = []
            for value, kind in [('Пробов', 'last_name'), ('Макетослав', 'first_name')]:
                start = text.index(value)
                entities.append(dict(message_id='m1', type=kind, start=start, end=start + len(value)))
            source = dict(synthetic_only=True, records=[dict(gold=dict(session_id='synthetic-test',
                messages=[dict(id='m1', text=text)], entities=entities))])
            snapshot = base / 'snapshot.json'
            snapshot.write_text(json.dumps(source), encoding='utf-8')
            (base / 'detector-recordings.json').write_text('[]', encoding='utf-8')
            (base / 'detector-recordings-identity.json').write_text('{}', encoding='utf-8')
            with patch.dict(evaluation.CORPORA, {'test': (snapshot, base)}), patch.object(
                    evaluation, 'record_detector', return_value=(EmptyDetector(), [])):
                summary = evaluation.evaluate('test', base / 'results')
            self.assertEqual(summary['common_filtered']['sessions'], 1)
            self.assertEqual(summary['common_filtered']['v1']['micro']['tp'], 0)
            self.assertEqual(summary['common_filtered']['v1']['micro']['fn'], 2)
            self.assertEqual(summary['common_filtered']['v2']['micro']['tp'], 2)
            self.assertEqual(summary['common_filtered']['v2']['micro']['fn'], 0)


if __name__ == '__main__':
    unittest.main()
