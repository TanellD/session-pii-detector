"""Риск после линковки: веса типов, а не confidence детектора или число людей."""
import json
from decimal import Decimal
from pathlib import Path
from typing import get_args

from pydantic import Field, field_validator

from tools.annotation_calculator import RED

from .components import Fact
from .models import EntityType, Model, RiskAssessment
from .normalization import fact_value

NAME_PARTS = {'first_name', 'last_name', 'patronymic'}
PAIRS = {frozenset(('first_name', 'last_name')): 'fi',
         frozenset(('first_name', 'patronymic')): 'io',
         frozenset(('last_name', 'patronymic')): 'fo'}


class RiskPolicy(Model):
    version: str = Field(min_length=1)
    person_threshold: float = Field(ge=0)
    unknown_threshold: float = Field(ge=0)
    weights: dict[str, float | None]

    @field_validator('weights')
    @classmethod
    def check_weights(cls, weights):
        from math import isfinite
        allowed = set(get_args(EntityType)) | {'fi', 'io', 'fo', 'fio'}
        if set(weights) - allowed:
            raise ValueError('Unknown risk weight type')
        if any(v is not None and (not isfinite(v) or not 0 <= v <= 1) for v in weights.values()):
            raise ValueError('Weights must be finite and in [0, 1]')
        return weights

    @classmethod
    def load(cls):
        path = Path(__file__).resolve().parents[2] / 'configs/runtime-risk-policy.json'
        return cls.model_validate(json.loads(path.read_text(encoding='utf-8')))


class RiskEvaluator:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy.load()

    def assess(self, facts: tuple[Fact, ...], *, unknown: bool = False) -> RiskAssessment:
        # Для человека повтор типа обновляет активное значение, не добавляет вес.
        # Неизвестные факты не считаем одним лицом: удаляем только точные повторы.
        personal = tuple(f for f in facts if not f.service_contact)
        if unknown:
            active = {(f.type, fact_value(f.type, f.value)): f for f in personal}
            kinds = [f.type for f in active.values()]
        else:
            typed = {fact_value(f.type, f.value) for f in personal if f.type in NAME_PARTS}
            tokens = {token for value in typed for token in value.split()}
            # Уточнение той же части/набора частей не даёт второй вес PERSON.
            personal = tuple(f for f in personal if f.type != 'person' or not (
                fact_value(f.type, f.value) in typed or
                (tokens and set(fact_value(f.type, f.value).split()) <= tokens)))
            kinds = list(dict.fromkeys(f.type for f in personal))
            parts = NAME_PARTS.intersection(kinds)
            if len(parts) >= 2:
                kinds = [k for k in kinds if k not in NAME_PARTS]
                kinds.append('fio' if len(parts) == 3 else PAIRS[frozenset(parts)])
        missing = tuple(sorted({k for k in kinds if self.policy.weights.get(k) is None}))
        # Десятичная арифметика сохраняет строгое сравнение на границе порога.
        total = None if missing else sum((Decimal(str(self.policy.weights[k])) for k in kinds), Decimal(0))
        score = None if total is None else float(total)
        threshold = self.policy.unknown_threshold if unknown else self.policy.person_threshold
        rules = []
        if any(f.type in RED for f in facts):
            rules.append('red_rule')
        # Сохраняем согласованный список; произвольные две оранжевые не достаточны.
        if not unknown:
            names = any(k in kinds for k in ('fi', 'io', 'fo', 'fio'))
            contact = any(k in kinds for k in ('phone', 'email', 'address'))
            if (names and contact) or {'phone', 'email'}.issubset(kinds):
                rules.append('combination_rule')
        if total is not None and total > Decimal(str(threshold)):
            rules.append('score_rule')
        return RiskAssessment(risk_score=score, threshold=threshold,
                              missing_weights=missing, rules=tuple(rules))
