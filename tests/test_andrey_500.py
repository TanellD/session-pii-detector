"""Сценарии, владельцы и диагностика утечек между синтетическими корпусами."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from build_andrey_500 import BANKS, build
from compare_synthetic_corpora import masked_messages, ownership_structure

# Независимые мини-источники без локального data/ и прежних выгрузок.
SHAPES={
 'dev-01':[[],[('first_name','reader'),('last_name','reader')],[('email','reader')]],
 'dev-02':[[('phone','visitor')],[('email','companion')],[('phone','visitor')]],
 'dev-03':[[],[]],
 'dev-04':[[('last_name','recipient'),('first_name','recipient'),('patronymic','recipient')],[('address','recipient')],[('address','recipient')]],
 'dev-05':[[],[],[]],
 'dev-06':[[('age','applicant')],[('phone','applicant')],[('email','applicant')]],
 'dev-07':[[('occupation','employee')],[('organization','employee')],[('sex','employee')]],
 'dev-08':[[],[('passport','renter')]],
 'test-01':[[('first_name','learner')],[('patronymic','learner')],[('phone','learner')]],
 'test-02':[[('first_name','buyer'),('last_name','buyer')],[('address','recipient')],[]],
 'test-03':[[('first_name','host'),('first_name','cohost')],[('phone',['host','cohost'])],[('email','host')]],
 'test-04':[[('email',None)],[('first_name','attendee')],[('phone','attendee')]],
 'test-05':[[],[],[]], 'test-06':[[('geography','applicant')],[],[]],
 'test-07':[[('age','candidate')],[('sex','candidate')],[('occupation','candidate')]],
 'test-08':[[('phone','holder'),('email','holder')],[('email','holder')],[]],
}


class Andrey500Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'data/tmp').mkdir(parents=True,exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tmp')
        cls.parent=Path(cls.temp.name)
        source=cls.parent/'source'
        source.mkdir()
        plan={'scenarios':[dict(id=sid,family=sid,split='development' if sid.startswith('dev') else 'test') for sid in SHAPES]}
        authored=[dict(id=sid,messages=[['source placeholder',*[[t,'placeholder',p] for t,p in row]] for row in rows]) for sid,rows in SHAPES.items()]
        for name,value in [('scenario-plan.json',plan),('authored-dialogues.json',authored)]:
            (source/name).write_text(json.dumps(value),encoding='utf-8')
        cls.output=cls.parent/'output'
        cls.manifest=build(source,source,cls.output)
        cls.gold=json.loads((cls.output/'gold.json').read_text(encoding='utf-8'))['annotations']
        cls.checks=json.loads((cls.output/'gold-assessments.json').read_text(encoding='utf-8'))['checks']
        cls.scenario={x['session_id']:x['source_scenario_id'] for x in cls.checks}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_only_original_scenarios_and_exact_size(self):
        self.assertEqual(self.manifest['sessions'],500)
        self.assertEqual(set(self.manifest['source_scenario_ids']),set(SHAPES))
        self.assertFalse(self.manifest['new_integration_scenarios_included'])
        for row in self.checks:
            self.assertEqual(row['split'],'development' if row['source_scenario_id'].startswith('dev') else 'test')

    def test_late_owner_and_different_people_do_not_create_false_combination(self):
        for row in self.checks:
            if row['source_scenario_id'] in {'test-04','dev-02','test-02'}:
                self.assertIsNone(row['author_expected_first_alert'])
        for doc in self.gold:
            if self.scenario[doc['session_id']]=='test-04':
                email=next(e for e in doc['entities'] if e['type']=='email')
                self.assertIsNone(email['person_id'])

    def test_shared_phone_keeps_email_single_and_negative_documents_absent(self):
        for doc in self.gold:
            if self.scenario[doc['session_id']]=='test-03':
                self.assertEqual(next(e for e in doc['entities'] if e['type']=='phone')['person_id'],['host','cohost'])
                self.assertEqual(next(e for e in doc['entities'] if e['type']=='email')['person_id'],'host')
            if self.scenario[doc['session_id']] in {'dev-03','test-06'}:
                self.assertFalse(any(e['type'] in {'individual_inn','snils','bank_card'} for e in doc['entities']))

    def test_value_masking_does_not_count_contact_substitution_as_diversity(self):
        def doc(value):
            return dict(messages=[dict(id='m1',text='Моя почта '+value+'.')],entities=[dict(message_id='m1',start=10,end=10+len(value),type='email',person_id='a')])
        self.assertEqual(masked_messages(doc('a@example.invalid')),masked_messages(doc('long@example.invalid')))

    def test_owner_structure_is_id_invariant_but_distinguishes_joint_ownership(self):
        left=dict(messages=[dict(id='m1',text='')],entities=[dict(message_id='m1',start=0,type='phone',person_id=['a','b'])])
        renamed=dict(messages=left['messages'],entities=[dict(message_id='m1',start=0,type='phone',person_id=['x','y'])])
        single=dict(messages=left['messages'],entities=[dict(message_id='m1',start=0,type='phone',person_id='x')])
        self.assertEqual(ownership_structure(left),ownership_structure(renamed))
        self.assertNotEqual(ownership_structure(left),ownership_structure(single))

    def test_author_ledger_covers_every_prefix(self):
        ledger=json.loads((self.output/'author-alert-ledger.json').read_text(encoding='utf-8'))['steps']
        self.assertEqual(len(ledger),sum(len(d['messages']) for d in self.gold))
        self.assertFalse(self.manifest['service_weights_used_for_gold'])


if __name__=='__main__':
    unittest.main()
