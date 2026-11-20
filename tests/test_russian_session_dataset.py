from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from build_russian_session_dataset import user_messages,russian_share
import review_russian_session_dataset as review_tool


class RussianSessionDatasetTests(unittest.TestCase):
    def test_plain_roles_drop_assistant_headers_and_next_timestamp_but_keep_digits(self):
        text='synthetic header\n**00:00**\nТ: Скрытый вопрос.\n\nК: 000-000-000 00.\n\n**00:01.5**\nТ: Ещё вопрос.\n\nК: Спасибо.\n'
        messages,origins=user_messages(text,2)
        self.assertEqual([m['text'] for m in messages],['000-000-000 00.','Спасибо.'])
        self.assertEqual([text[o['start']:o['end']] for o in origins],[m['text'] for m in messages])
        self.assertNotIn('Скрытый',str(messages))

    def test_inline_timestamp_roles_and_prefix_limit(self):
        text='**00:00:01 Т:** Привет.\n\n**00:00:02 К:** Добрый день.\n\n**00:00:03 Т:** Будущий вопрос.\n\n**00:00:04 К:** Будущий ответ.'
        messages,origins=user_messages(text,1)
        self.assertEqual(messages,[{'id':'m1','text':'Добрый день.'}])
        self.assertEqual(origins[0]['source_turn_index'],2)

    def test_cyrillic_fraction_is_prefix_level_not_numeric_message_filter(self):
        self.assertGreater(russian_share('Мой номер телефона: 00000000000'),0.7)
        self.assertEqual(russian_share('Only English text'),0)

    def test_refresh_derived_never_overwrites_frozen_review(self):
        with tempfile.TemporaryDirectory() as directory:
            frozen=Path(directory)/'blind-review-frozen.json'
            frozen.write_text('{"original":true}',encoding='utf-8')
            with patch.object(review_tool,'REFRESH',True),patch.object(review_tool,'VERIFY',False):
                with self.assertRaises(ValueError):
                    review_tool.write(frozen,{'original':False})
            self.assertEqual(frozen.read_text(encoding='utf-8'),'{"original":true}')

    def test_blind_freeze_rejects_already_started_run(self):
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)/'run'
            run.mkdir()
            with self.assertRaisesRegex(ValueError,'до API'):
                review_tool.freeze(Path(directory)/'source',run)
