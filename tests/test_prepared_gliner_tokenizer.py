"""Offline-интеграция tokenizer: исходная конвертация и Unicode-путь."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
BACKBONE=ROOT/'outputs/gliner-prepared/backbone'


@unittest.skipUnless((BACKBONE/'tokenizer.json').exists() and importlib.util.find_spec('transformers'),
                     'Нужны локальная GLiNER и её окружение')
class PreparedTokenizerTests(unittest.TestCase):
    def test_unicode_absolute_path_matches_original_conversion(self):
        from transformers import AutoTokenizer
        texts=['Меня зовут Тестимир.', 'Мой телефон +7 (000) 000-00-01.', 'Синтетиков Макетимир 🙂']
        result=subprocess.run([sys.executable,'-X','utf8','-c',
            "import json,sys;from transformers import AutoTokenizer;"
            "t=AutoTokenizer.from_pretrained('.',local_files_only=True,from_slow=True);"
            "print(json.dumps([t.encode(s) for s in json.loads(sys.argv[1])]))",json.dumps(texts)],
            cwd=BACKBONE,capture_output=True,text=True,encoding='utf-8',check=True)
        tokenizer=AutoTokenizer.from_pretrained(str(BACKBONE.resolve()),local_files_only=True)
        self.assertTrue(tokenizer.is_fast)
        self.assertEqual([tokenizer.encode(s) for s in texts],json.loads(result.stdout))


if __name__=='__main__':
    unittest.main()
