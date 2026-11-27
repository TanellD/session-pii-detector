"""Локальное исследование отчеств ФНС и СРО; ПДн сохраняются только в игнорируемом кеше."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
from html.parser import HTMLParser
import io
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

from urllib.request import Request, build_opener
from collect_vk_name_pairs import NoRedirect
from research_name_pairs import ROOT, write_private
from pilot_wikipedia_fullnames import patronymic_form
from assess_vk_pair_sample import observed_lift
from analyze_vk_annotation_evidence import common_pair_holdout, fit_common_pairs, score_common_pairs, ALPHAS
from collect_vk_group_members import pair_summary

SRO_URL = 'https://kadastrsro.ru/index.php/reestr/'
SRO_CACHE = ROOT / 'sro-cadastral-minimal-2026-11-17.json'
FNS_CACHE = ROOT / 'fns-rsmp-2026-11-10.json'


def normalized(value):
    return ' '.join(value.casefold().replace('ё', 'е').split())


def certificate_value(value):
    values = set(re.findall(r'(?<!\d)\d{2}-\d{2}-\d+(?!\d)', normalized(value)))
    return next(iter(values)) if len(values) == 1 else ''


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
        self.headers, self.cell_tag = [], None
        self.page_numbers = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'input' and attributes.get('name') == 'filter[page]' and attributes.get('value', '').isdigit():
            self.page_numbers.append(int(attributes['value']))
        if tag == 'tr':
            self.row = []
        elif tag == 'th' or (tag == 'td' and self.row is not None):
            self.cell = []
            self.cell_tag = tag

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            value = ' '.join(''.join(self.cell).split())
            if self.cell_tag == 'th':
                self.headers.append(value)
            if self.row is not None:
                self.row.append(value)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row, self.cell = None, None


def reference_rows(html):
    parser = TableRows()
    parser.feed(html)
    expected = ['фио', 'дата вступления', 'номер аттестата', 'реестровый номер', 'статус инженера']
    started = [normalized(v) for v in parser.headers[:5]] == expected
    records = []
    for row in parser.rows:
        if len(row) >= 5 and normalized(row[0]) == 'фио':
            started = True
            continue
        if started and len(row) >= 5 and len(row[0].split()) == 3:
            records.append({'fio': normalized(row[0]), 'certificate': normalized(row[2]), 'status': normalized(row[4])})
    if len(records) < 3:
        raise ValueError('No supported HTML reference table')
    return records


def column_mapping(rows, reference):
    """Infer CSV positions only by exact overlap with the labelled HTML table."""
    names = {r['fio'] for r in reference}
    certificates = {r['certificate'] for r in reference if r['certificate']}
    width = max(map(len, rows))
    fio_scores = {}
    cert_scores = {}
    for start in range(width):
        for span in (1, 3):
            fio_scores[(start, span)] = sum(normalized(' '.join(row[start:start + span])) in names for row in rows)
        cert_scores[start] = sum(start < len(row) and certificate_value(row[start]) in certificates for row in rows)
    best_fio = max(fio_scores.values())
    best_cert = max(cert_scores.values())
    f = [key for key, value in fio_scores.items() if value == best_fio]
    c = [key for key, value in cert_scores.items() if value == best_cert]
    if best_fio < 3 or best_cert < 3 or len(f) != 1 or len(c) != 1:
        print(json.dumps({'stage': 'mapping_rejected', 'csv_rows': len(rows), 'columns': width,
                          'best_name_matches': best_fio, 'best_certificate_matches': best_cert,
                          'name_candidate_columns': f, 'certificate_candidate_columns': c}), flush=True)
        raise ValueError('Ambiguous or unverified CSV field mapping')
    return {'fio_start': f[0][0], 'fio_span': f[0][1], 'certificate_column': c[0],
            'html_name_matches': best_fio, 'html_certificate_matches': best_cert}


def parse_sro(csv_raw, html):
    rows = [row for row in csv.reader(io.StringIO(csv_raw.decode('utf-8-sig')), delimiter=';') if any(v.strip() for v in row)]
    mapping = column_mapping(rows, reference_rows(html))
    grouped, quality = defaultdict(list), Counter()
    for row in rows:
        start, span, cert_col = mapping['fio_start'], mapping['fio_span'], mapping['certificate_column']
        parts = normalized(' '.join(row[start:start + span])).split()
        certificate = certificate_value(row[cert_col]) if cert_col < len(row) else ''
        if len(parts) != 3 or not all(re.fullmatch(r'[а-я]+(?:-[а-я]+)*', part) for part in parts):
            quality['unsupported_fullname'] += 1
            continue
        if not re.fullmatch(r'\d{2}-\d{2}-\d+', certificate):
            quality['missing_or_unsupported_certificate'] += 1
            continue
        grouped[certificate].append(tuple(parts))
    records = []
    for certificate, values in grouped.items():
        distinct = set(values)
        if len(distinct) != 1:
            quality['conflicting_name_certificate_groups'] += 1
            quality['rows_in_conflicting_groups'] += len(values)
            continue
        quality['duplicate_rows_same_certificate_and_name'] += len(values) - 1
        triple = next(iter(distinct))
        records.append({**dict(zip(('surname', 'name', 'patronymic'), triple)),
                        'local_key': hashlib.sha256(('sro:' + certificate).encode()).hexdigest()})
    if not records:
        raise ValueError('No supported deduplicated SRO records')
    return {'source': 'cadastral_engineers_guild_csv', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'url': SRO_URL + '?download=csv', 'csv_sha256': hashlib.sha256(csv_raw).hexdigest(),
            'html_sha256': hashlib.sha256(html.encode()).hexdigest(), 'csv_bytes': len(csv_raw),
            'raw_registry_rows': len(rows), 'verified_mapping': mapping, 'quality': dict(quality), 'records': records,
            'scope': 'All membership statuses; certificate/name concordance dedup, ambiguous groups excluded; not national people counts'}


def fetch(url, bound, allow_prefix=False):
    with build_opener(NoRedirect()).open(Request(url, headers={'User-Agent': 'SessionPiiResearch/0.1'}), timeout=45) as response:
        chunks, size, started, complete = [], 0, time.monotonic(), False
        while size <= bound and time.monotonic() - started < 90:
            chunk = response.read1(min(65536, bound + 1 - size))
            if not chunk:
                complete = True
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b''.join(chunks)
    if allow_prefix:
        if not complete:
            # CSV fields can contain newlines: use the CSV parser below and
            # discard the final possibly unfinished record there.
            raw = raw[:bound]
        return raw, complete
    if not complete:
        raise ValueError('Response exceeds collection size or time bound')
    if len(raw) > bound:
        raise ValueError('Response exceeds collection bound')
    return raw


def download_sro():
    if SRO_CACHE.exists():
        return {'source': 'sro', 'status': 'using_existing_cache'}
    html = fetch(SRO_URL, 2000000).decode('utf-8-sig')
    reference_rows(html)  # Reject changed HTML before fetching the larger CSV.
    raw, complete = fetch(SRO_URL + '?download=csv', 64000000, allow_prefix=True)
    received_sha256, received_bytes = hashlib.sha256(raw).hexdigest(), len(raw)
    print(json.dumps({'stage': 'received_csv', 'bytes': received_bytes, 'complete': complete}), flush=True)
    if not complete:
        # Keep only complete CSV records, including quoted multiline fields.
        text = raw.decode('utf-8-sig', errors='ignore')
        rows = []
        try:
            for row in csv.reader(io.StringIO(text), delimiter=';', strict=True):
                rows.append(row)
        except csv.Error:
            pass
        # Even an unquoted incomplete last row can parse successfully.
        rows = rows[:-1]
        out = io.StringIO()
        csv.writer(out, delimiter=';').writerows(rows)
        raw = out.getvalue().encode('utf-8')
    payload = parse_sro(raw, html)
    payload['received_prefix_sha256'] = received_sha256
    payload['received_bytes'] = received_bytes
    payload['csv_hash_scope'] = 'original CSV' if complete else 're-serialized complete CSV records from bounded prefix'
    payload['download_complete'] = complete
    payload['scope'] += '; bounded prefix pilot if download_complete=false'
    write_private(SRO_CACHE, payload)
    return {'source': 'sro', 'status': 'cached', 'raw_registry_rows': payload['raw_registry_rows'],
            'accepted_records': len(payload['records']), 'quality': payload['quality'], 'mapping': payload['verified_mapping']}


def merge_sro_records(records):
    grouped, quality, result = defaultdict(list), Counter(), []
    for record in records:
        grouped[record['local_key']].append(record)
    for values in grouped.values():
        if len({tuple(r[k] for k in ('surname', 'name', 'patronymic')) for r in values}) != 1:
            quality['conflicting_name_certificate_groups'] += 1
            quality['rows_in_conflicting_groups'] += len(values)
            continue
        quality['duplicate_rows_same_certificate_and_name'] += len(values) - 1
        result.append(values[0])
    return result, dict(quality)


def download_sro_html():
    if SRO_CACHE.exists():
        return {'source': 'sro', 'status': 'using_existing_cache'}
    html = fetch(SRO_URL, 2000000).decode('utf-8-sig')
    reference_rows(html)
    parser = TableRows()
    parser.feed(html)
    last_page = max(parser.page_numbers, default=1)
    if not 2 <= last_page <= 100:
        raise ValueError('Unsupported pagination bounds')

    def page(number):
        path = ROOT / 'sro-html-pages-2026-11-17' / f'page-{number}.json'
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        text = html if number == 1 else fetch(SRO_URL + f'?filter%5Bpage%5D={number}', 2000000).decode('utf-8-sig')
        refs, accepted, quality = reference_rows(text), [], Counter()
        for r in refs:
            parts, certificate = normalized(r['fio']).split(), certificate_value(r['certificate'])
            if len(parts) != 3 or not all(re.fullmatch(r'[а-я]+(?:-[а-я]+)*', part) for part in parts):
                quality['unsupported_fullname'] += 1
                continue
            if not certificate:
                quality['missing_or_unsupported_certificate'] += 1
                continue
            accepted.append({**dict(zip(('surname', 'name', 'patronymic'), parts)),
                             'local_key': hashlib.sha256(('sro:' + certificate).encode()).hexdigest()})
        payload = {'page': number, 'checked_at': datetime.now(timezone.utc).isoformat(),
                   'html_sha256': hashlib.sha256(text.encode()).hexdigest(), 'raw_registry_rows': len(refs),
                   'quality': dict(quality), 'records': accepted}
        write_private(path, payload)
        return payload

    pages = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for payload in executor.map(page, range(1, last_page + 1)):
            pages.append(payload)
            print(json.dumps({'stage': 'sro_page', 'page': payload['page'], 'planned_pages': last_page,
                              'accepted_rows': len(payload['records'])}), flush=True)
    fingerprints = {hashlib.sha256(json.dumps(p['records'], sort_keys=True).encode()).hexdigest() for p in pages}
    if len(fingerprints) != len(pages):
        raise ValueError('Repeated pagination response')
    records, quality = merge_sro_records([r for p in pages for r in p['records']])
    aggregate_quality = Counter(quality)
    for p in pages:
        aggregate_quality.update(p['quality'])
    if not records:
        raise ValueError('No supported deduplicated SRO records')
    payload = {'source': 'cadastral_engineers_guild_html', 'checked_at': datetime.now(timezone.utc).isoformat(),
               'url': SRO_URL, 'download_complete': True, 'pages': [{k: v for k, v in p.items() if k != 'records'} for p in pages],
               'raw_registry_rows': sum(p['raw_registry_rows'] for p in pages), 'quality': dict(aggregate_quality),
               'records': records, 'scope': 'All listed HTML pages, all membership statuses; certificate/name concordance dedup; unsupported records excluded; not national counts'}
    write_private(SRO_CACHE, payload)
    return {'source': 'sro', 'status': 'cached', 'pages': len(pages), 'raw_registry_rows': payload['raw_registry_rows'],
            'accepted_records': len(records), 'quality': payload['quality']}


def io_records(records):
    result = []
    for r in records:
        triple = tuple(normalized(r[k]) for k in ('surname', 'name', 'patronymic'))
        form = patronymic_form(triple)
        result.append({'surname': triple[1], 'name': triple[2],
                       'sex': {'masculine_form': 2, 'feminine_form': 1}.get(form)})
    return result


def io_dependence(records):
    result = {key.replace('FI', 'IO'): value for key, value in pair_summary(records).items()}
    result['scope'] = 'Given-name/patronymic written pairs in one register fragment; form code is not observed sex; no population extrapolation'
    result['form_code_counts'] = result.pop('sex_counts', {})
    result['records'] = result.pop('accounts')
    result.pop('country_id_counts', None)
    return result


def describe(records):
    rows = [tuple(normalized(r[k]) for k in ('surname', 'name', 'patronymic')) for r in records]
    n = len(rows)
    result = {'records': n, 'written_patronymic_form_counts': dict(Counter(patronymic_form(r) for r in rows))}
    for label, indices in (('O', (2,)), ('IO', (1, 2)), ('FO', (0, 2)), ('FIO', (0, 1, 2))):
        cells = Counter(tuple(row[i] for i in indices) for row in rows)
        result[label] = {'distinct_cells': len(cells), 'singleton_cells': sum(c == 1 for c in cells.values()),
                         'max_count': max(cells.values()), 'cells_count_ge_5': sum(c >= 5 for c in cells.values()),
                         'cells_count_ge_20': sum(c >= 20 for c in cells.values()),
                         'record_share_in_cells_ge_5': sum(c for c in cells.values() if c >= 5) / n}
    transformed = io_records(records)
    result['IO_dependence'] = io_dependence(transformed)
    result['IO_within_written_form'] = {}
    for sex, label in ((1, 'feminine_form'), (2, 'masculine_form')):
        subset = [r for r in transformed if r['sex'] == sex]
        if len(subset) >= 20:
            result['IO_within_written_form'][label] = io_dependence(subset)
    result['IO_observed_lift'] = observed_lift([(r['surname'], r['name']) for r in transformed])
    result['IO_holdout'] = [common_pair_holdout(transformed, minimum, seed) for minimum in (3, 5) for seed in range(42, 47)]
    result['IO_holdout_summary'] = []
    for minimum in (3, 5):
        runs = [r for r in result['IO_holdout'] if r['minimum_train_pair_count'] == minimum]
        valid = [r for r in runs if r.get('test_joint_minus_independent_bits') is not None]
        result['IO_holdout_summary'].append({'minimum_train_pair_count': minimum, 'valid_runs': len(valid),
            'strict_improvements': sum(r['test_joint_minus_independent_bits'] < 0 for r in valid),
            'mean_joint_minus_independent_bits': sum(r['test_joint_minus_independent_bits'] for r in valid) / len(valid) if valid else None,
            'mean_selected_pair_test_share': sum(r['test_independent']['selected_pair_rows'] / r['test_rows'] for r in valid) / len(valid) if valid else None})
    return result


def transfer(source_records, target_records):
    source = io_records(source_records)
    rows = sorted(((r['sex'], r['surname'], r['name']) for r in source), key=lambda x: (str(x[0]), x[1], x[2]))
    random.Random(42).shuffle(rows)
    train, validation = rows[:int(.6 * len(rows))], rows[int(.6 * len(rows)):int(.8 * len(rows))]
    models = fit_common_pairs(train, 5)
    alpha = min(ALPHAS, key=lambda a: score_common_pairs(validation, models, a)['mean_log_loss_bits'])
    target = [(r['sex'], r['surname'], r['name']) for r in io_records(target_records)]
    baseline, mixed = score_common_pairs(target, models, 0), score_common_pairs(target, models, alpha)
    return {'source_fit_rows': len(train), 'source_validation_rows': len(validation), 'target_rows': len(target),
            'source_selected_alpha': alpha, 'independent': baseline, 'joint_mixture': mixed,
            'joint_minus_independent_bits': mixed['mean_log_loss_bits'] - baseline['mean_log_loss_bits'],
            'scope': 'Administrative source transfer, possible person overlap; conditional on written patronymic form, not observed sex'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-sro', action='store_true')
    args = parser.parse_args()
    if args.download_sro:
        try:
            status = download_sro_html()
            write_private(ROOT / 'sro-last-attempt.json', status)
            print(json.dumps(status, ensure_ascii=False))
        except Exception as error:
            failure = {'status': 'sro_download_failed', 'error_type': type(error).__name__}
            safe_reasons = {'No supported HTML reference table', 'Ambiguous or unverified CSV field mapping',
                            'No supported deduplicated SRO records', 'Response exceeds collection size or time bound',
                            'Response exceeds collection bound', 'Unsupported pagination bounds', 'Repeated pagination response'}
            if str(error) in safe_reasons:
                failure['reason'] = str(error)
            write_private(ROOT / 'sro-last-attempt.json', failure)
            print(json.dumps(failure))
            raise SystemExit(2)
        return
    sources, report = {}, {'checked_at': datetime.now(timezone.utc).isoformat(), 'network_used': False, 'sources': {}}
    for label, path in (('fns', FNS_CACHE), ('sro', SRO_CACHE)):
        if not path.exists():
            report['sources'][label] = {'status': 'cache_missing'}
            continue
        raw = path.read_bytes()
        payload = json.loads(raw)
        sources[label] = payload['records']
        print(json.dumps({'stage': 'analyzing', 'source': label, 'records': len(payload['records'])}), flush=True)
        report['sources'][label] = {'cache_sha256': hashlib.sha256(raw).hexdigest(), 'scope': payload['scope'],
                                    **describe(payload['records'])}
    if len(sources) == 2:
        report['cross_source_IO'] = {'fns_to_sro': transfer(sources['fns'], sources['sro']),
                                   'sro_to_fns': transfer(sources['sro'], sources['fns'])}
        report['cross_source_IO_without_matching_fullnames'] = {}
        for source, target in (('fns', 'sro'), ('sro', 'fns')):
            key = lambda r: tuple(normalized(r[k]) for k in ('surname', 'name', 'patronymic'))
            source_names = {key(r) for r in sources[source]}
            retained = [r for r in sources[target] if key(r) not in source_names]
            report['cross_source_IO_without_matching_fullnames'][source + '_to_' + target] = {
                'excluded_target_rows_matching_fullname': len(sources[target]) - len(retained),
                **transfer(sources[source], retained)}
    report['limitations'] = ['Written patronymic form is not independently observed sex',
                             'No validated national population frequencies or <=100 candidate decision',
                             'Sources not pooled; rare and nonstandard patronymics may be excluded']
    write_private(ROOT / 'fns-sro-patronymic-results-2026-11-17.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
