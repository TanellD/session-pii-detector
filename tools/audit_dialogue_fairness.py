"""Локальные признаки подсказок и смещений; не автоматическое доказательство реалистичности."""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re


def normalized(record):
    by_message = {}
    for entity in record['gold']['entities']:
        by_message.setdefault(entity['message_id'], []).append(entity)
    texts = []
    for message in record['gold']['messages']:
        text = message['text']
        for entity in sorted(by_message.get(message['id'], []), key=lambda e:e['start'], reverse=True):
            text = text[:entity['start']]+' ENTITY_'+entity['type']+' '+text[entity['end']:]
        texts.append(text)
    return re.findall(r'\w+', ' '.join(texts).casefold())


def audit(source):
    records = source['records']
    cues = re.compile(r'не\s+(?:объедин\w*|назнач\w*|копир\w*|перенос\w*|добавля\w*|счита\w*|прикрепля\w*)|ост\w*\s+без\s+владельц\w*', re.I)
    grammar = re.compile(r'(?:участника|заявителя|заказчика|покупателя|посетителя)\s*$', re.I)
    rows, vectors = [], {}
    for record in records:
        doc = record['gold']
        texts = {m['id']:m['text'] for m in doc['messages']}
        cue_messages = [dict(message_id=m['id'], text=m['text']) for m in doc['messages'] if cues.search(m['text'])]
        grammar_flags = []
        for entity in doc['entities']:
            if entity['type'] == 'first_name' and grammar.search(texts[entity['message_id']][:entity['start']]):
                grammar_flags.append(dict(message_id=entity['message_id'],
                    quote=texts[entity['message_id']][max(0, entity['start']-45):entity['end']],
                    note='Проверить падеж имени и согласование роли; это кандидат для ручного разбора.'))
        codes = []
        for text in texts.values():
            codes += [int(n) for n in re.findall(r'(?:pilot(\d+)@example\.invalid)', text)]
            codes += [int(n) for n in re.findall(r'\+7 \(000\) (\d{3})-89-\d{2}', text)]
            codes += [int(n) for n in re.findall(r'Пробный контур (\d+)', text)]
            codes += [int(n) for n in re.findall(r'ул\. Макетная, д\. (\d+)', text)]
        rows.append(dict(session_id=record['session_id'], split=record['split'], difficulty=record['difficulty'],
            positive=record['expected_first_alert'] is not None, messages=len(doc['messages']),
            owner_instruction_cues=cue_messages, grammar_review=grammar_flags,
            synthetic_codes=sorted(set(codes)), source_paragraph_layout=record['source_paragraph_layout']))
        vectors[record['session_id']] = normalized(record)
    cross_pairs = []
    for i, left in enumerate(records):
        for right in records[i+1:]:
            if left['split'] == right['split']:
                continue
            ratio = SequenceMatcher(None, vectors[left['session_id']], vectors[right['session_id']], autojunk=False).ratio()
            cross_pairs.append(dict(left=left['session_id'],right=right['session_id'],similarity=round(ratio,4)))
    cross_pairs.sort(key=lambda r:r['similarity'],reverse=True)
    codes_positive = [n for r in rows if r['positive'] for n in r['synthetic_codes']]
    codes_negative = [n for r in rows if not r['positive'] for n in r['synthetic_codes']]
    by_difficulty = {d:dict(sessions=sum(r['difficulty']==d for r in rows),
        sessions_with_owner_instruction_cues=sum(r['difficulty']==d and bool(r['owner_instruction_cues']) for r in rows),
        sessions_with_grammar_candidates=sum(r['difficulty']==d and bool(r['grammar_review']) for r in rows),
        message_counts=dict(Counter(r['messages'] for r in rows if r['difficulty']==d))) for d in ('easy','medium','hard')}
    all_types = sorted({e['type'] for r in records for e in r['gold']['entities']})
    type_coverage = {}
    for part in ('train','validation','test'):
        counts = Counter(e['type'] for r in records if r['split']==part for e in r['gold']['entities'])
        type_coverage[part] = dict(counts=dict(counts), missing_observed_types=[t for t in all_types if not counts[t]])
    catalog = json.loads((Path(__file__).resolve().parents[1]/'configs/annotation-classes.json').read_text(encoding='utf-8'))
    return dict(sessions=len(rows), by_difficulty=by_difficulty, type_coverage=type_coverage,
        missing_taxonomy_types=sorted(set(catalog['classes'])-set(all_types)),
        synthetic_code_outcome_confound=dict(
            positive_sessions_with_code=sum(r['positive'] and bool(r['synthetic_codes']) for r in rows),
            negative_sessions_with_code=sum(not r['positive'] and bool(r['synthetic_codes']) for r in rows),
            positive_range=[min(codes_positive),max(codes_positive)],
            negative_range=[min(codes_negative),max(codes_negative)],
            strictly_separated=max(codes_positive)<min(codes_negative),
            interpretation='Коды входят в исходный текст. Разделение диапазонов по метке — искусственный признак генератора, не свойство риска.'),
        cross_split_similarity_top20=cross_pairs[:20],
        similarity_note='SequenceMatcher по словам после замены значений сущностей типами; сходство — повод для разбора, не доказательство копирования.',
        sessions_with_owner_instruction_cues=sum(bool(r['owner_instruction_cues']) for r in rows),
        sessions_with_grammar_candidates=sum(bool(r['grammar_review']) for r in rows), cases=rows)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    source=json.loads(args.input.read_text(encoding='utf-8'))
    if source.get('synthetic_only') is not True:
        parser.error('Only synthetic inputs')
    root=Path(__file__).resolve().parents[1]
    if not args.output.resolve().is_relative_to(root/'data'):
        parser.error('Output must remain in data/')
    result=audit(source)
    result['input_sha256']=hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'cases','cross_split_similarity_top20'}},ensure_ascii=False,indent=2))
