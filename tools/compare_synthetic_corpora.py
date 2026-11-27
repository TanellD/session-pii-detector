"""Сравнение двух авторских корпусов: разнообразие, повторы, покрытие, ограничения объединения."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics

from annotation_contract import validate
from run_annotation_batch import checksum_issues


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def masked_messages(doc):
    entities=defaultdict(list)
    for entity in doc['entities']:
        entities[entity['message_id']].append(entity)
    result=[]
    for message in doc['messages']:
        text=message['text']
        for entity in sorted(entities[message['id']],key=lambda e:e['start'],reverse=True):
            text=text[:entity['start']]+' TYPE_'+entity['type']+' '+text[entity['end']:]
        text=re.sub(r'Заявка TEST-\d+\.\s*','',text)
        # Числовые отвлекающие детали и синтетические индексы не создают разнообразия.
        tokens=re.findall(r'[a-zа-яё_]+|\d+',text.casefold())
        result.append(' '.join('num' if t.isdigit() else t for t in tokens))
    return result


def ownership_structure(doc):
    owners={}
    def ids(value):
        if value is None:
            return None
        result=[]
        for owner in value if isinstance(value,list) else [value]:
            if owner not in owners:
                owners[owner]=f'p{len(owners)+1}'
            result.append(owners[owner])
        return sorted(result)
    by_message=defaultdict(list)
    for entity in sorted(doc['entities'],key=lambda e:(int(e['message_id'][1:]),e['start'])):
        by_message[entity['message_id']].append([entity['type'],ids(entity['person_id'])])
    # Нейтральные реплики не меняют последовательность раскрытия атрибутов.
    return json.dumps([by_message[m['id']] for m in doc['messages'] if by_message[m['id']]],sort_keys=True)


def inspect(source):
    manifest=read(source/'manifest.json')
    gold=read(source/'gold.json')['annotations']
    checks=read(source/'gold-assessments.json')['checks']
    inputs=read(source/'sessions.json')['sessions']
    if len(inputs)!=len(gold) or len({s['session_id'] for s in inputs})!=len(inputs):
        raise ValueError('Повреждённый корпус')
    input_by_id={s['session_id']:s for s in inputs}
    by_check={c['session_id']:c for c in checks}
    types=Counter()
    rows=[]
    age_grammar_issues=[]
    for doc in gold:
        validate(doc)
        if checksum_issues(doc):
            raise ValueError('Невалидная checksum в авторском корпусе')
        if doc['messages']!=input_by_id[doc['session_id']]['messages']:
            raise ValueError('Авторская разметка изменила исходный текст')
        types.update(e['type'] for e in doc['entities'])
        masked=masked_messages(doc)
        tokens=' '.join(masked).split()
        grams={tuple(tokens[i:i+3]) for i in range(max(0,len(tokens)-2))}
        for message in doc['messages']:
            for match in re.finditer(r'\b(\d+)\s+(год|года|лет)\b',message['text']):
                age=int(match[1])
                expected='лет' if 11<=age%100<=14 or age%10 not in {1,2,3,4} else 'год' if age%10==1 else 'года'
                if match[2]!=expected:
                    age_grammar_issues.append(dict(session_id=doc['session_id'],message_id=message['id'],issue='age_unit_agreement'))
        rows.append(dict(session_id=doc['session_id'],family=by_check[doc['session_id']]['family'],
                         split=by_check[doc['session_id']].get('split'),masked='\n'.join(masked),grams=grams,
                         structure=ownership_structure(doc)))
    masked_counts=Counter(r['masked'] for r in rows)
    source_text=['\n'.join(m['text'] for m in d['messages']) for d in gold]
    message_lengths=[len(d['messages']) for d in gold]
    unknown=sum(any(e['person_id'] is None for e in d['entities']) for d in gold)
    shared=sum(any(isinstance(e['person_id'],list) for e in d['entities']) for d in gold)
    many_people=0
    for doc in gold:
        owners=set()
        for e in doc['entities']:
            owners.update(e['person_id'] if isinstance(e['person_id'],list) else [e['person_id']] if e['person_id'] else [])
        many_people+=len(owners)>1
    summary=dict(version=manifest['version'],sessions=len(gold),messages=sum(message_lengths),entities=sum(types.values()),
        families=len(manifest['families']),positive_sessions=sum(bool(c['assessment']['first_alert']) for c in checks),
        message_count_min=min(message_lengths),message_count_median=statistics.median(message_lengths),message_count_max=max(message_lengths),
        mean_messages=statistics.mean(message_lengths),median_session_characters=statistics.median(map(len,source_text)),
        unique_surface_sessions=len(set(source_text)),unique_value_masked_sessions=len(masked_counts),
        value_masked_duplicate_excess=len(rows)-len(masked_counts),
        unique_value_masked_messages=len({m for d in gold for m in masked_messages(d)}),
        unique_ownership_structures=len({r['structure'] for r in rows}),
        sessions_unknown_owner=unknown,sessions_joint_owners=shared,sessions_multiple_people=many_people,
        extraction_types=dict(sorted(types.items())),age_grammar_issues=len(age_grammar_issues),
        policy_version=manifest['policy_version'],schema_versions=sorted({d['schema_version'] for d in gold}),
        human_review_status=manifest['human_review_status'])
    return summary,rows,age_grammar_issues


def compare(first,second,threshold=.85):
    a,ar,ai=inspect(first)
    b,br,bi=inspect(second)
    rows=[{**r,'corpus':'first'} for r in ar]+[{**r,'corpus':'second'} for r in br]
    within=Counter()
    examples=[]
    cross=0
    participants=defaultdict(set)
    for i,left in enumerate(rows):
        for right in rows[i+1:]:
            if not left['grams'] or not right['grams']:
                continue
            # Максимальный возможный Jaccard при разной длине множеств.
            if min(len(left['grams']),len(right['grams']))/max(len(left['grams']),len(right['grams']))<threshold:
                continue
            intersection=len(left['grams'] & right['grams'])
            similarity=intersection/(len(left['grams'])+len(right['grams'])-intersection)
            if similarity>=threshold:
                kind='cross' if left['corpus']!=right['corpus'] else left['corpus']
                within[kind]+=1
                participants[kind].update([left['session_id'],right['session_id']])
                if kind=='cross':
                    cross+=1
                if len(examples)<30 or kind=='cross' and sum(e['kind']=='cross' for e in examples)<30:
                    examples.append(dict(left=left['session_id'],right=right['session_id'],kind=kind,jaccard=round(similarity,5)))
    structure_a={r['structure'] for r in ar}
    structure_b={r['structure'] for r in br}
    shared_structures=structure_a & structure_b
    exact_cross=set(r['masked'] for r in ar)&set(r['masked'] for r in br)
    return dict(first=a,second=b,definition=dict(
        value_masking='replace author spans by type; remove TEST ticket prefix; normalize digits/case/punctuation',
        near_duplicate='Jaccard over sets of consecutive word trigrams of value-masked session',threshold=threshold,
        caveat='lexical diagnostics, not validated measures of naturalness or independent scenarios'),
        near_duplicate_pairs=dict(within),near_duplicate_participants={k:len(v) for k,v in participants.items()},
        examples=examples,cross_exact_masked_patterns=len(exact_cross),
        shared_ownership_structure_patterns=len(shared_structures),
        second_sessions_with_first_structure=sum(r['structure'] in shared_structures for r in br),
        first_only_types=sorted(set(a['extraction_types'])-set(b['extraction_types'])),
        second_only_types=sorted(set(b['extraction_types'])-set(a['extraction_types'])),
        grammar_diagnostics=dict(first=ai,second=bi),
        merge=dict(schema_compatible=a['schema_versions']==b['schema_versions'],policy_compatible=a['policy_version']==b['policy_version'],
                   session_ids_disjoint=not ({r['session_id'] for r in ar}&{r['session_id'] for r in br}),
                   auto_merged=False,approval_as_independent_benchmark=False,
                   required=['retain corpus and source scenario metadata','exclude unresolved annotation disagreements from gold',
                             'group source scenario variants and near duplicates before splitting',
                             'check semantic scenario overlap across sources; original splits cannot simply be concatenated',
                             'keep first diagnostic and second conversational results separate']))


def markdown(result):
    a,b=result['first'],result['second']
    lines=['# Сравнение первого и второго синтетических наборов','',
           'Это анализ авторских данных. Завершение внешней разметки проверяется отдельно; независимой человеческой проверки нет.','',
           '| Показатель | Первый | Второй по Андрею |','|---|---:|---:|']
    for title,key in [('Сессии','sessions'),('Сообщения','messages'),('Спаны','entities'),('Семейства','families'),
        ('Среднее сообщений','mean_messages'),('Различные тексты сессий','unique_surface_sessions'),
        ('Различные сессии после маскирования значений','unique_value_masked_sessions'),
        ('Лишние повторы после маскирования','value_masked_duplicate_excess'),
        ('Сессии с несколькими людьми','sessions_multiple_people'),('Сессии с общими владельцами','sessions_joint_owners'),
        ('Сессии с неизвестным владельцем','sessions_unknown_owner'),('Положительные по авторскому ТЗ','positive_sessions'),
        ('Ошибки склонения единицы возраста','age_grammar_issues')]:
        lines.append(f'| {title} | {a[key]} | {b[key]} |')
    lines += ['',f"Близкие пары (Jaccard ≥ {result['definition']['threshold']}): {result['near_duplicate_pairs']}.",
        f"Полностью одинаковых шаблонов между источниками после маскирования: {result['cross_exact_masked_patterns']}.",
        f"Схем владельцев и порядка раскрытия атрибутов, общих для двух наборов: {result['shared_ownership_structure_patterns']}.",
        f"Классы только первого набора: {result['first_only_types']}.",'',
        'Первый набор подходит для проверок конкретных правил и регрессий. Второй добавляет разные формулировки в прикладных сценариях поддержки. '
        'Разнообразие формулировок не равно независимости сюжетов: второй содержит только исходные сценарии Андрея.','',
        'Оба набора содержат только пользовательские сообщения, вымышленные имена и заведомо нерабочие контакты. '
        'Они не подтверждают качество на полном диалоге с ботом, опечатках, неявной принадлежности и реальном распределении обращений. '
        'Числа лексического сходства не являются метрикой реалистичности.','',
        'Технически объединение возможно по общей схеме и политике. Объединять в единый независимый тест рано: '
        'нужно завершить внешнюю проверку, разобрать расхождения и заново разделить объединённые сценарные группы. '
        'Второй набор пока не проверяет положительное извлечение ИНН, СНИЛС и карты: исходные сценарии содержат невалидные значения. '
        'Для задач сервиса наборы дополняют друг друга, но исходники и отдельные результаты следует сохранить.','',
        'Первый корпус размечается Gemma, второй Qwen. Разность согласия с автором нельзя трактовать как эффект генератора. '
        'Для такого вывода потребуется один и тот же разметчик на сопоставимом срезе обоих источников.','',
        'Проверяемый JSON с определениями, примерами пар и ограничениями: comparison.json.']
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first',type=Path,required=True)
    parser.add_argument('--second',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    target=args.output.resolve()
    root=Path(__file__).resolve().parents[1]
    if target.exists() or not target.is_relative_to(root/'data'):
        parser.error('Нужен новый каталог внутри data/')
    result=compare(args.first,args.second)
    result['source_hashes']={str(p):hashlib.sha256((p/'sessions.json').read_bytes()).hexdigest() for p in [args.first,args.second]}
    target.mkdir(parents=True)
    (target/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (target/'comparison.md').write_text(markdown(result),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'grammar_diagnostics','examples'}},ensure_ascii=False))
