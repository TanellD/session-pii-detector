"""Перенос модельной конфигурации без изменения источника."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.relocate_prepared_model import relocate


class ModelRelocationTests(unittest.TestCase):
    def test_relocation_retains_source_and_weight_hash(self):
        (ROOT/'outputs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as directory:
            source, output = Path(directory)/'source', Path(directory)/'docker'
            (source/'backbone').mkdir(parents=True)
            (source/'model.safetensors').write_bytes(b'synthetic-unit-not-model-weights')
            (source/'backbone/config.json').write_text('{}')
            (source/'gliner_config.json').write_text(json.dumps({'model_name': str(source/'backbone')}), encoding='utf-8')
            config = json.loads((ROOT/'benchmarks/pii_smoke/config.json').read_text(encoding='utf-8'))
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            hashes = {p.relative_to(source).as_posix(): digest(p) for p in source.rglob('*') if p.is_file()}
            manifest = {k: config[k] for k in ['model', 'revision', 'backbone', 'backbone_revision']}
            manifest['files'] = hashes
            (source/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            result = relocate(source, output)
            self.assertEqual(json.loads((output/'gliner_config.json').read_text())['model_name'], '/models/gliner/backbone')
            for name, expected in hashes.items():
                self.assertEqual(digest(source/name), expected)
            self.assertEqual(result['files']['model.safetensors'], hashes['model.safetensors'])


if __name__ == '__main__':
    unittest.main()
