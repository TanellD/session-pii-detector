"""Дешёвые контрольные линкеры; не рекомендации для рабочего сервиса."""


def all_unknown(request):
    return {'assignments': [dict(candidate_id=c['candidate_id'], owners=[], service_contact=False)
                            for c in request['candidates']]}


def one_person(request):
    person = request['known_people'][0]['person_id'] if request['known_people'] else 'new:1'
    return {'assignments': [dict(candidate_id=c['candidate_id'], owners=[person], service_contact=False)
                            for c in request['candidates']]}
