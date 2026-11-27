"""Копия подготовленной модели для фиксированной точки монтирования Docker."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.pii_smoke.prepare import read_manifest, sha256


def relocate(source, output, runtime_dir='/models/gliner'):
    source, output = source.resolve(), output.resolve()
    if output.exists() or not output.is_relative_to(ROOT/'outputs'):
        raise ValueError('Use a new output directory under outputs/')
    if not PurePosixPath(runtime_dir).is_absolute() or '..' in PurePosixPath(runtime_dir).parts:
        raise ValueError('Use an absolute POSIX runtime directory')
    settings = json.loads((ROOT/'benchmarks/pii_smoke/config.json').read_text(encoding='utf-8'))
    original = read_manifest(source, settings)
    for name in original['files']:
        src, dst = source/name, output/name
        if not src.resolve().is_relative_to(source) or not dst.resolve().is_relative_to(output):
            raise ValueError('Invalid model manifest path')
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == '.safetensors':
            try:
                os.link(src, dst)
                continue
            except OSError:
                pass
        shutil.copyfile(src, dst)
    config_path = output/'gliner_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['model_name'] = str(PurePosixPath(runtime_dir)/'backbone')
    config_path.write_text(json.dumps(config, indent=2)+'\n', encoding='utf-8')
    manifest = dict(original, relocated_from_manifest_sha256=sha256(source/'manifest.json'),
                    runtime_model_dir=runtime_dir)
    manifest['files'] = {name: sha256(output/name) for name in original['files']}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/gliner-docker')
    parser.add_argument('--runtime-dir', default='/models/gliner')
    args = parser.parse_args()
    relocate(args.source, args.output, args.runtime_dir)
    print('Prepared Docker model copy with verified file hashes.')
