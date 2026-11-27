"""Скачать закреплённые публичные веса диапазонами и проверить SHA-256.

Обход зависания большого GET на CDN. URL берётся из официального HF API;
размер и ETag фиксируются до скачивания. Тексты и секреты не используются.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import time


def download(config_path, cache, parts, workers=4):
    import truststore
    truststore.inject_into_ssl()
    import requests
    from huggingface_hub import get_hf_file_metadata, hf_hub_url
    config = json.loads(config_path.read_text(encoding='utf-8'))
    metadata = get_hf_file_metadata(hf_hub_url(config['model'],'model.safetensors',
        revision=config['revision']),token=False,timeout=30)
    if not re.fullmatch('[0-9a-f]{64}',metadata.etag) or not metadata.size:
        raise ValueError('Ожидался SHA-256 ETag и известный размер')
    identity = dict(model=config['model'],revision=config['revision'],size=metadata.size,sha256=metadata.etag)
    parts.mkdir(parents=True,exist_ok=True)
    marker=parts/'source.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != identity:
        raise ValueError('Каталог частей относится к другой версии весов')
    marker.write_text(json.dumps(identity,indent=2),encoding='utf-8')
    block=8*1024*1024
    count=(metadata.size+block-1)//block

    def part(index):
        start=index*block
        end=min(metadata.size,start+block)-1
        target=parts/f'part-{index:04d}.bin'
        if target.exists() and target.stat().st_size==end-start+1:
            return target.stat().st_size
        for attempt in range(4):
            try:
                with requests.get(metadata.location,headers={'Range':f'bytes={start}-{end}',
                    'Cache-Control':'no-cache'},stream=True,timeout=(20,45)) as response:
                    if response.status_code!=206 or response.headers.get('Content-Range')!=f'bytes {start}-{end}/{metadata.size}':
                        raise ValueError(f'Неверный диапазон ответа для части {index}: HTTP {response.status_code}')
                    temporary=target.with_suffix('.tmp')
                    with temporary.open('wb') as output:
                        for chunk in response.iter_content(256*1024):
                            output.write(chunk)
                    if temporary.stat().st_size!=end-start+1:
                        raise ValueError(f'Неверная длина части {index}')
                    temporary.replace(target)
                return end-start+1
            except (requests.RequestException,ValueError):
                if attempt==3:
                    raise
                time.sleep(1+attempt)

    total=0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending={executor.submit(part,i):i for i in range(count)}
        for completed,future in enumerate(as_completed(pending),1):
            total+=future.result()
            print(f'Parts {completed}/{count}; {total}/{metadata.size} bytes',flush=True)
    repository=cache/'hub'/('models--'+config['model'].replace('/','--'))
    blobs=repository/'blobs'
    blobs.mkdir(parents=True,exist_ok=True)
    target=blobs/metadata.etag
    temporary=parts/'assembled.tmp'
    digest=hashlib.sha256()
    with temporary.open('wb') as output:
        for index in range(count):
            with (parts/f'part-{index:04d}.bin').open('rb') as source:
                while chunk:=source.read(1024*1024):
                    digest.update(chunk)
                    output.write(chunk)
    if digest.hexdigest()!=metadata.etag or temporary.stat().st_size!=metadata.size:
        raise ValueError('SHA-256 собранного файла не совпадает с закреплённым ETag')
    temporary.replace(target)
    snapshot=repository/'snapshots'/config['revision']/'model.safetensors'
    snapshot.parent.mkdir(parents=True,exist_ok=True)
    if not snapshot.exists():
        os.link(target,snapshot)
    else:
        with snapshot.open('rb') as source:
            if hashlib.file_digest(source,'sha256').hexdigest()!=metadata.etag:
                raise ValueError('Существующий snapshot содержит другие веса')
    print('Pinned weights downloaded; SHA-256 verified',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path('benchmarks/pii_smoke/config.json'))
    parser.add_argument('--cache',type=Path,default=Path('.cache-research/huggingface'))
    parser.add_argument('--parts',type=Path,default=Path('data/tmp/gliner-weights-chunks'))
    args=parser.parse_args()
    download(args.config,args.cache,args.parts)
