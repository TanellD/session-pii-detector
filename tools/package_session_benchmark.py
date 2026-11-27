"""Локальный архив готовых выгрузок с проверкой замороженных SHA256."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, target = args.dataset.resolve(), args.output.resolve()
    if not source.is_relative_to(ROOT/'data') or not target.is_relative_to(ROOT/'data'):
        parser.error('Dataset and archive must remain in data/')
    if target.exists() or target.is_relative_to(source):
        parser.error('Use a new archive outside the source directory')
    seal = json.loads((source/'frozen-sha256.json').read_text(encoding='utf-8'))
    for name, digest in seal.items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen artifact changed: '+name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive checksum error')
        for name, digest in seal.items():
            if hashlib.sha256(archive.read(name.replace('\\', '/'))).hexdigest() != digest:
                raise ValueError('Archive content differs: '+name)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix+'.sha256').write_text(digest+'  '+target.name+'\n', encoding='utf-8')
    print(json.dumps(dict(path=str(target), bytes=target.stat().st_size, sha256=digest, frozen_artifacts_verified=len(seal)), indent=2))


if __name__ == '__main__':
    main()
