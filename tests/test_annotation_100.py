import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from annotation_contract import validate
from report_annotation_100 import report


class AnnotationHundredTests(unittest.TestCase):
    def test_subset_cannot_relabel_non_synthetic_source(self):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp') as directory:
            path = Path(directory)
            (path/'sessions.json').write_text('{"synthetic_only":false,"sessions":[]}',encoding='utf-8')
            result = subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/subset_annotation_family.py'),
                                     '--source',str(path),'--output',str(path/'subset'),'--family','inn'],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((path/'subset').exists())

    def test_report_refuses_unfinished_run(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run/'manifest.json').write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'не завершён'):
                report(run,run)

    def test_generated_gold_covers_classes_and_frozen_late_owner(self):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data/tmp') as directory:
            target = Path(directory)/'set'
            subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/build_annotation_100.py'),
                            '--output',str(target)],check=True,capture_output=True)
            read = lambda name: json.loads((target/name).read_text(encoding='utf-8'))
            gold = read('gold.json')['annotations']
            checks = read('gold-assessments.json')['checks']
            self.assertEqual(len(gold),100)
            self.assertEqual(len(read('sessions.json')['sessions']),100)
            for doc in gold:
                validate(doc)
            self.assertEqual({e['type'] for d in gold for e in d['entities']},
                             {'first_name','last_name','patronymic','occupation','organization','phone',
                              'email','address','bank_card','passport','snils','individual_inn','age','sex','geography'})
            by_id = {d['session_id']:d for d in gold}
            for check in checks:
                if check['family']=='late_owner':
                    doc = by_id[check['session_id']]
                    self.assertIsNone(doc['entities'][0]['person_id'])
                    self.assertEqual(check['assessment']['first_alert']['message_id'],'m3')
                if check['family']=='possessive':
                    self.assertEqual(by_id[check['session_id']]['entities'],[])
            subset = Path(directory)/'subset'
            subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/subset_annotation_family.py'),
                            '--source',str(target),'--output',str(subset),'--family','inn'],check=True,capture_output=True)
            exported = json.loads((subset/'gold.json').read_text(encoding='utf-8'))['annotations']
            self.assertEqual(len(exported),5)
            for doc in exported:
                self.assertEqual(doc,by_id[doc['session_id']])
                self.assertIsNone(doc['entities'][0]['person_id'])


if __name__ == '__main__':
    unittest.main()
