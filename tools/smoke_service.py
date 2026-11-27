"""HTTP smoke: создание, анализ, retry и чтение истории; только синтетический текст."""
import argparse
import json
from urllib.request import Request, urlopen
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--expect-adapter', action='store_true')
    parser.add_argument('--timeout', type=float, default=10, help='HTTP timeout in seconds')
    args = parser.parse_args()

    def call(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(args.base_url.rstrip('/')+path,data=body,
                          headers={'Content-Type':'application/json'} if body else {})
        with urlopen(request,timeout=args.timeout) as response:
            return json.load(response)

    health = call('/healthz')
    state = call('/v1/sessions',{'session_id':'synthetic-smoke-'+uuid4().hex})
    payload = dict(session_id=state['session_id'],session_epoch=state['session_epoch'],expected_state_version=0,
                   message=dict(message_id=1,role='user',text='Как отключить уведомления?'))
    response = call('/v1/analyze',payload)
    retry = call('/v1/analyze',payload)
    snapshot = call('/v1/sessions/'+state['session_id'])
    assert response==retry
    assert snapshot['state_version']==1 and len(snapshot['message_history'])==1
    assert not response['verdict']['session_alert']
    if args.expect_adapter:
        assert health['mode'] in ('regex', 'gliner')
        extracted = call('/v1/analyze', dict(session_id=state['session_id'],
            session_epoch=state['session_epoch'], expected_state_version=1,
            message=dict(message_id=2, role='user', text='СНИЛС: 000-000-001 01.')))
        assert [e['type'] for e in extracted['entities']] == ['snils']
        assert extracted['entities'][0]['mention_keys'][0]['session_id'] == state['session_id']
        assert extracted['verdict']['first_alert_message_id'] == 2
        assert extracted['verdict']['first_alert_reason'] == 'red_rule'
    print(json.dumps(dict(status='ok' ,mode=health['mode'],state_version=snapshot['state_version'],
                          exact_retry=True,extraction_checked=args.expect_adapter,coverage_complete=response['verdict']['coverage_complete'],
                          incomplete_reasons=response['verdict']['incomplete_reasons']),ensure_ascii=False))


if __name__=='__main__':
    main()
