"""Перенести исходники локального корпуса в новые правила без перезаписи gold."""
import argparse
import copy
import json
from pathlib import Path

from prepare_session_corpus import compile_corpus, write_json, digest
from session_alert_review import REVIEW_POLICY, REVIEW_SCHEMA, DOCUMENT_CHECKSUMS
from benchmarks.pii_smoke.phone_format import valid_phone_span


def migrate(plan, authored, version):
    plan, authored = copy.deepcopy(plan), copy.deepcopy(authored)
    plan.update(version=version, review_policy=REVIEW_POLICY)
    scenarios = {s['id']: s for s in plan['scenarios']}
    changes = []
    for dialogue in authored:
        scenario = scenarios[dialogue['id']]
        if len(dialogue['messages']) != len(scenario['steps']):
            raise ValueError('Messages and plan steps differ')
        for index, row in enumerate(dialogue['messages']):
            text, *mentions = row
            kept = []
            for mention in mentions:
                kind, value, owner = mention
                start = text.index(value)
                check = DOCUMENT_CHECKSUMS.get(kind)
                reason = ('checksum' if check and not check(value) else
                          'phone_format' if kind == 'phone' and not valid_phone_span(text, start, start+len(value)) else
                          'out_of_scope' if kind in {'date_of_birth', 'relationship', 'special_category'} else None)
                if reason:
                    changes.append(dict(session_id=dialogue['id'], message_id=f'm{index+1}',
                                        type=kind, reason=reason))
                    continue
                kept.append(mention)
            dialogue['messages'][index] = [text, *kept]
            scenario['steps'][index] = [f"{'shared' if isinstance(m[2], list) else m[2] or 'unknown'}:{m[0]}" for m in kept]
    return plan, authored, changes


def add_review_scenarios(plan, authored):
    """Контролируемые синтетические сценарии; не независимая выборка."""
    cases = [
      ('v3-dev-shared-phone', 'development', [
        ['Я Тестимир, второй участник Макетимир.', ['first_name','Тестимир','a'], ['first_name','Макетимир','b']],
        ['Наш общий личный телефон +7 (000) 000-00-71.', ['phone','+7 (000) 000-00-71',['a','b']]],
        ['Повторю общий личный телефон +7 (000) 000-00-71.', ['phone','+7 (000) 000-00-71',['a','b']]],
        ['Моя личная почта joint@example.invalid.', ['email','joint@example.invalid','a']]]),
      ('v3-dev-shared-address', 'development', [
        ['Меня зовут Пробомир, второго жильца зовут Схемомир.', ['first_name','Пробомир','a'], ['first_name','Схемомир','b']],
        ['Мы оба живём по адресу г. Макетоград, ул. Пробная, д. 1.', ['address','г. Макетоград, ул. Пробная, д. 1',['a','b']]],
        ['Моя фамилия Примеров.', ['last_name','Примеров','a']]]),
      ('v3-dev-valid-inn', 'development', [['Мой ИНН: 000000012373.', ['individual_inn','000000012373','a']]]),
      ('v3-dev-context', 'development', [['Меня зовут Черновимир, фамилия Макетонов.', ['first_name','Черновимир','a'], ['last_name','Макетонов','a']]]),
      ('v3-test-valid-snils', 'test', [['Мой СНИЛС: 000-000-001 01.', ['snils','000-000-001 01','a']]]),
      ('v3-test-valid-card', 'test', [['Моя карта: 0000 0000 0000 0000.', ['bank_card','0000 0000 0000 0000','a']]]),
      ('v3-test-phone-formats', 'test', [
        ['Телефон: abc12. Другой: +7 (000 000-00-00.'],
        ['Мой телефон 80000000000.', ['phone','80000000000','a']]]),
      ('v3-test-generic-person', 'test', [
        ['Контакт подписан «Образимир Макетный».', ['person','Образимир Макетный','a']],
        ['Личная почта этого контакта generic@example.invalid.', ['email','generic@example.invalid','a']]])]
    existing = {x['id'] for x in plan['scenarios']}
    for sid, split, messages in cases:
        if sid in existing:
            raise ValueError('Review scenarios already present')
        plan['scenarios'].append(dict(id=sid, split=split, family=sid,
            profiles=[sid+'-profile'], steps=[
                [f"{'shared' if isinstance(m[2], list) else m[2] or 'unknown'}:{m[0]}" for m in row[1:]]
                for row in messages]))
        authored.append(dict(id=sid, messages=messages))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='session-alerts-v3')
    parser.add_argument('--add-review-scenarios', action='store_true')
    args = parser.parse_args()
    target = args.output.resolve()
    root = Path(__file__).resolve().parents[1]
    if target.exists() or not target.is_relative_to(root / 'data'):
        parser.error('Нужен новый каталог внутри data/; исходный набор сохраняется')
    source = args.source
    read = lambda name: json.loads((source / name).read_text(encoding='utf-8'))
    plan, authored, changes = migrate(read('scenario-plan.json'), read('authored-dialogues.json'), args.version)
    if args.add_review_scenarios:
        add_review_scenarios(plan, authored)
    documents, _ = compile_corpus(plan, authored)
    target.mkdir(parents=True)
    write_json(target / 'scenario-plan.json', plan)
    write_json(target / 'authored-dialogues.json', authored)
    write_json(target / 'migration-report.json', dict(
        source_sessions_sha256=digest(source / 'sessions.json'),
        schema=REVIEW_SCHEMA, review_policy=REVIEW_POLICY, removed=changes,
        sessions=len(documents), gold_status='requires_new_author_ledger_and_independent_review'))
    print(json.dumps(dict(status='migrated_sources', sessions=len(documents), removed=len(changes),
                          gold_reused=False), ensure_ascii=False))


if __name__ == '__main__':
    main()
