"""Инварианты авторского корпуса и совместимость схемы с локальной проверкой."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from build_annotation_1000 import build
from run_annotation_batch import checksum_issues, prepare, report
from run_compatible_annotation_pilot import materialize_quotes, quote_schema
from run_llm_annotation_pilot import annotation_schema


class Annotation1000Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT/'data/tmp')
        cls.source = Path(cls.temp.name)/'source'
        cls.manifest = build(cls.source)
        cls.gold = json.loads((cls.source/'gold.json').read_text(encoding='utf-8'))['annotations']
        checks = json.loads((cls.source/'gold-assessments.json').read_text(encoding='utf-8'))['checks']
        cls.family = {x['session_id']:x['family'] for x in checks}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_exact_size_and_gold_excluded_from_model_source(self):
        source = json.loads((self.source/'sessions.json').read_text(encoding='utf-8'))
        self.assertEqual(len(source['sessions']),1000)
        self.assertEqual(set(self.manifest['families'].values()),{25})
        for session in source['sessions']:
            self.assertEqual(set(session),{'session_id','messages'})
            self.assertNotIn('entities',session)
        self.assertEqual(len({x['session_id'] for x in self.gold}),1000)

    def test_shared_owners_are_lists_without_merging_personal_email(self):
        for doc in self.gold:
            if self.family[doc['session_id']]=='shared_phone_personal_email':
                phone = next(e for e in doc['entities'] if e['type']=='phone')
                email = next(e for e in doc['entities'] if e['type']=='email')
                self.assertEqual(set(phone['person_id']),{'self','colleague'})
                self.assertEqual(email['person_id'],'self')

    def test_late_revelation_does_not_reassign_previous_null(self):
        for doc in self.gold:
            if self.family[doc['session_id']]=='late_owner_remention':
                emails = [e for e in doc['entities'] if e['type']=='email']
                self.assertIsNone(emails[0]['person_id'])
                self.assertEqual(emails[1]['person_id'],'self')

    def test_invalid_checksums_absent_and_all_positive_documents_valid(self):
        for doc in self.gold:
            self.assertEqual(checksum_issues(doc),[])
            if self.family[doc['session_id']].startswith('invalid_'):
                self.assertEqual(doc['entities'],[])

    def test_batches_keep_unknown_unattempted_and_reject_overwrite(self):
        target = Path(self.temp.name)/'batches'
        state = prepare(self.source,target,100)
        self.assertEqual(state['sessions'],1000)
        summary = report(target)
        self.assertEqual(summary['compared_valid_sessions'],0)
        self.assertFalse(summary['all_batches_finished'])
        with self.assertRaises(ValueError):
            prepare(self.source,target,100)

    def test_transport_grammar_omits_unsupported_keyword_but_local_rejects_duplicates(self):
        transport = quote_schema()['properties']['entities']['items']['properties']['person_id']['anyOf']
        canonical = annotation_schema()['properties']['entities']['items']['properties']['person_id']['anyOf']
        self.assertTrue(next(v for v in canonical if v['type']=='array')['uniqueItems'])
        self.assertNotIn('uniqueItems',next(v for v in transport if v['type']=='array'))
        doc = next(d for d in self.gold if self.family[d['session_id']]=='shared_phone_personal_email')
        raw = copy.deepcopy(doc)
        raw['schema_version']='annotation-quotes-v1'
        texts = {m['id']:m['text'] for m in raw['messages']}
        for entity in raw['entities']:
            entity['mention']=texts[entity['message_id']][entity.pop('start'):entity.pop('end')]
            entity['occurrence']=1
            if isinstance(entity['person_id'],list):
                entity['person_id']=['self','self']
        source = {k:doc[k] for k in ('session_id','messages')}
        with self.assertRaises(ValueError):
            materialize_quotes(raw,source)


if __name__=='__main__':
    unittest.main()
