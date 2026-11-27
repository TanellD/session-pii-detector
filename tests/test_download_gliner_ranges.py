"""Проверка диапазонов и хеша без сети и настоящих весов."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.download_gliner_ranges import download


@unittest.skipUnless(importlib.util.find_spec('huggingface_hub') and importlib.util.find_spec('truststore'),
                     'Требуется окружение GLiNER')
class RangeDownloadTests(unittest.TestCase):
    def run_download(self,data,expected=None,content_range=None):
        expected=data if expected is None else expected
        digest=hashlib.sha256(expected).hexdigest()
        class Response:
            status_code=206
            headers={'Content-Range':content_range or f'bytes 0-{len(data)-1}/{len(data)}'}
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def iter_content(self,size): return iter([data])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'config.json'
            config.write_text(json.dumps(dict(model='synthetic/model',revision='pinned')),encoding='utf-8')
            with (patch('huggingface_hub.get_hf_file_metadata',return_value=SimpleNamespace(
                    etag=digest,size=len(data),location='https://example.invalid/weights')),
                 patch('huggingface_hub.hf_hub_url',return_value='https://example.invalid/metadata'),
                 patch('requests.get',return_value=Response()) as get,
                 patch('truststore.inject_into_ssl'),patch('tools.download_gliner_ranges.time.sleep')):
                download(config,root/'cache',root/'parts',workers=1)
                self.assertEqual(get.call_args.kwargs['headers']['Range'],f'bytes=0-{len(data)-1}')
            self.assertEqual((root/'cache/hub/models--synthetic--model/snapshots/pinned/model.safetensors').read_bytes(),expected)

    def test_exact_download_and_verified_snapshot(self):
        self.run_download(b'synthetic weights')

    def test_reject_wrong_range(self):
        with self.assertRaisesRegex(ValueError,'Неверный диапазон'):
            self.run_download(b'synthetic weights',content_range='bytes 1-18/19')

    def test_reject_corrupted_assembled_hash(self):
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            self.run_download(b'synthetic weights',expected=b'corrupted weights')


if __name__=='__main__':
    unittest.main()
