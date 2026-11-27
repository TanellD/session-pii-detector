"""Проверки воспроизводимости независимого назначения и явных общих фактов."""
import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_generation_v2_trial import profiles
from build_difficulty_pilot import materialize


class GenerationV2TrialTests(unittest.TestCase):
    def test_profiles_preserve_only_explicit_shared_relationships(self):
        values = profiles(random.Random(739182))
        self.assertNotEqual(values['a']['phone'], values['b']['phone'])
        messages, entities, _, _ = materialize([
            'Общий телефон [[ab:phone]].',
            'Без подписи [[u:phone]].',
            '[[b:first]]: телефон [[b:phone]].',
        ], values)
        self.assertEqual(entities[0]['person_id'], ['a', 'b'])
        self.assertIsNone(entities[1]['person_id'])
        self.assertEqual(entities[-1]['person_id'], 'b')
        self.assertEqual(messages[1]['text'].split()[-1], messages[2]['text'].split()[-1])

    def test_seed_reproduces_all_values(self):
        self.assertEqual(profiles(random.Random(91)), profiles(random.Random(91)))
        self.assertNotEqual(profiles(random.Random(91)), profiles(random.Random(92)))


if __name__ == '__main__':
    unittest.main()
