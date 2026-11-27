"""Короткая диагностика API без вывода ключа или персональных данных."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError

from run_compatible_annotation_pilot import endpoint, local_key, send


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('env_file',type=Path,nargs='?')
    parser.add_argument('request_file',type=Path,nargs='?')
    parser.add_argument('--model',default='Gemma-4-31B-IT')
    args=parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root/'configs/llm-annotation-providers.json').read_text(encoding='utf-8'))['radeon']
    # Разрешает использовать ключ исходной рабочей копии без его копирования в worktree.
    key = local_key(profile['api_key_env'])
    if not key and args.env_file:
        for line in args.env_file.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name,value = line.split('=',1)
                if name.strip()==profile['api_key_env']:
                    key=value.strip().strip('"').strip("'")
    if not key:
        print(json.dumps(dict(status='missing_key')))
        return
    body = dict(model=args.model,messages=[dict(role='user',content='Return only JSON: {"ok":true}')],
                max_tokens=32,temperature=0)
    if args.request_file:
        body=json.loads(args.request_file.read_text(encoding='utf-8'))
    try:
        result=send(endpoint(profile['base_url']),key,body,45)
        print(json.dumps(dict(status='ok',model=result.get('model'))))
    except HTTPError as error:
        detail = error.read().decode('utf-8','replace').replace(key,'[REDACTED]')
        detail = re.sub(r'Bearer\s+\S+','Bearer [REDACTED]',detail)
        print(json.dumps(dict(status=error.code,retry_after=error.headers.get('Retry-After'),detail=detail[:900]),ensure_ascii=False))
    except (URLError,TimeoutError) as error:
        print(json.dumps(dict(status='network_error',error_type=type(error).__name__)))


if __name__=='__main__':
    main()
