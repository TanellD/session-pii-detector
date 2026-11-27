"""Сравнить сохранённые regex/GLiNER-прогоны на одинаковых успешных сессиях."""
import argparse
from collections import Counter
from pathlib import Path
import statistics

from evaluate_random_linker import alerts, extraction, read, write


def compare(regex,gliner):
    a,b=read(regex/'evaluation.json'),read(gliner/'evaluation.json')
    assert a['input_sha256']==b['input_sha256']
    docs=read(gliner/'corrected-gold.json')
    expected=[x['first_alert']['message_index'] if x['first_alert'] else None
              for x in read(gliner/'corrected-assessments.json')]
    rows=[]
    for run in b['runs']:
        seed=run['seed']
        left,right=[read(p/f'predictions-seed-{seed}.json') for p in (regex,gliner)]
        assert len(left)==len(right)==len(docs)
        selected=[i for i,(x,y) in enumerate(zip(left,right)) if not x.get('runtime_error') and not y.get('runtime_error')]
        metrics={}
        for name,predictions in [('regex',left),('gliner',right)]:
            metrics[name]=dict(alerts=alerts([expected[i] for i in selected],[predictions[i]['first_alert'] for i in selected]),
                extraction=extraction([docs[i] for i in selected],[predictions[i]['entities'] for i in selected]))
        rows.append(dict(seed=seed,common_sessions=len(selected),metrics=metrics))
    def averages(values,keys=('precision','recall','f2')):
        return {key:statistics.mean(value[key] for value in values) for key in keys}
    return dict(total_sessions=len(docs),same_gold_sha256=a['input_sha256'],
        scope='conditional on common fully processed sessions; not full service quality on all sessions',
        mean_f2_timely={name:statistics.mean(r['metrics'][name]['alerts']['timely']['f2'] for r in rows)
                        for name in ('regex','gliner')},
        mean_alert_metrics={name:{kind:averages([r['metrics'][name]['alerts'][kind] for r in rows])
                            for kind in ('session','timely')} for name in ('regex','gliner')},
        mean_linker_metrics=averages([r['linker_isolated'] for r in b['runs']],('precision','recall','f1')),
        mean_gold_spans_random_linker={kind:averages([r['gold_spans_random_linker'][kind] for r in b['runs']])
                                      for kind in ('session','timely')},
        raw_extraction_all_sessions=dict(regex=a['raw_detector_extraction'],gliner=b['raw_detector_extraction']),
        runtime_failures=Counter(e['reason'] for e in b['runs'][0]['runtime_failures']),runs=rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regex',type=Path,required=True)
    parser.add_argument('--gliner',type=Path,required=True)
    args=parser.parse_args()
    result=compare(args.regex,args.gliner)
    write(args.gliner/'comparison-with-regex.json',result)
    print({k:result[k] for k in ('total_sessions','mean_alert_metrics','mean_linker_metrics',
                                'mean_gold_spans_random_linker','runtime_failures')})
