"""Согласование корпуса, адаптера и качественных правил без весов сервиса."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from annotation_calculator import Calculator
from migrate_session_corpus import migrate
from prepare_session_corpus import compile_corpus, compile_gold
from session_alert_review import REVIEW_POLICY, review_steps
from run_session_baselines import run_ner


class CorpusFinalizationTests(unittest.TestCase):
    def sources(self):
        plan = dict(version='test-new', review_policy=REVIEW_POLICY, scenarios=[dict(
            id='joint', split='test', family='joint-fixture', profiles=['a', 'b'],
            steps=[['a:first_name', 'b:first_name'], ['shared:phone'], ['a:email']])])
        authored = [dict(id='joint', messages=[
            ['Я Тестимир, второй участник Макетимир.',
             ['first_name', 'Тестимир', 'a'], ['first_name', 'Макетимир', 'b']],
            ['Наш общий личный телефон +7 (000) 000-00-01.', ['phone', '+7 (000) 000-00-01', ['a', 'b']]],
            ['Моя почта synthetic@example.invalid.', ['email', 'synthetic@example.invalid', 'a']]])]
        return plan, authored

    def test_shared_personal_contact_is_evidence_for_one_owners_pair(self):
        plan, authored = self.sources()
        documents, _ = compile_corpus(plan, authored)
        steps = review_steps(documents[0], Calculator(policy_only=True))
        self.assertEqual([s['reason'] for s in steps], [None, None, 'combination_rule'])
        ledger = [dict(session_id='joint', steps=[
            [None, [], 'insufficient_statistics', 'Только имена'],
            [None, [], 'insufficient_statistics', 'Одного контакта недостаточно'],
            ['combination_rule', ['m2-e1', 'm3-e1'], None, 'У a есть личный телефон и почта']])]
        labels, disagreements = compile_gold(documents, ledger, Calculator(policy_only=True), reviewed=True)
        self.assertEqual(disagreements, [])
        self.assertEqual(labels[0]['t_gold'], 3)

    def test_migration_keeps_sources_and_removes_invalid_document_gold(self):
        plan, authored = self.sources()
        plan['scenarios'][0]['steps'].append(['a:snils'])
        authored[0]['messages'].append(['СНИЛС: 000-000-000 16', ['snils', '000-000-000 16', 'a']])
        original = copy.deepcopy((plan, authored))
        new_plan, new_authored, changes = migrate(plan, authored, 'new-version')
        documents, _ = compile_corpus(new_plan, new_authored)
        self.assertEqual((plan, authored), original)
        self.assertEqual(documents[0]['entities'][2]['person_id'], ['a', 'b'])
        self.assertEqual(new_plan['scenarios'][0]['steps'][-1], [])
        self.assertEqual(changes[0]['reason'], 'checksum')

    def test_reviewed_baseline_uses_adapter_context_and_real_checksum_filter(self):
        plan, authored = self.sources()
        documents, _ = compile_corpus(plan, authored)
        config = {'labels': {}, 'threshold': .5}
        predictions, diagnostics = run_ner(documents, config, None,
            {'PHONE': 'phone', 'EMAIL': 'email'}, {'joint': 'test'}, reviewed=True)
        self.assertEqual([e['type'] for e in predictions[0]['entities']], ['phone', 'email'])
        self.assertEqual(diagnostics['discarded'], {})

    def test_gold_never_uses_service_weights(self):
        plan, authored = self.sources()
        documents, _ = compile_corpus(plan, authored)
        self.assertEqual(review_steps(documents[0], Calculator(policy_only=True))[-1]['reason'], 'combination_rule')
