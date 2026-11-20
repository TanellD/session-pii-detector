"""Публичный контракт HTTP; спаны используют Unicode code points [start, end)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal['first_name', 'last_name', 'patronymic', 'sex', 'age',
                     'geography', 'occupation', 'organization', 'phone', 'email',
                     'address', 'passport', 'snils', 'individual_inn', 'bank_card']
Rule = Literal['red_rule', 'combination_rule', 'candidate_count_rule', 'score_rule']


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class CreateSession(Model):
    session_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class Message(Model):
    message_id: int = Field(ge=1)
    role: Literal['user'] = 'user'
    text: str = Field(min_length=1, max_length=20000)


class AnalyzeRequest(Model):
    session_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    session_epoch: str = Field(min_length=1, max_length=64)
    expected_state_version: int = Field(ge=0)
    message: Message


class ResetRequest(Model):
    session_epoch: str = Field(min_length=1, max_length=64)
    expected_state_version: int = Field(ge=0)


class Span(Model):
    type: EntityType
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    score: float | None = None  # Исходная оценка компонента, не вероятность ПДн.


class Candidate(Span):
    sources: tuple[str, ...] = ()


class Entity(Candidate):
    id: str
    person_id: str | None
    service_contact: bool = False


class RiskAssessment(Model):
    risk_score: float | None
    threshold: float
    missing_weights: tuple[str, ...] = ()
    rules: tuple[Rule, ...] = ()


class Verdict(Model):
    session_alert: bool = False
    current_alert: bool = False
    first_alert_message_id: int | None = None
    first_alert_reason: Rule | None = None
    annotation_uncertain: bool = False
    annotation_uncertain_ever: bool = False
    clarification_reason: Literal['insufficient_statistics'] | None = None
    coverage_complete: bool = True
    incomplete_reasons: tuple[str, ...] = ()
    candidate_counts: dict[str, float | None] = Field(default_factory=dict)
    person_risks: dict[str, RiskAssessment] = Field(default_factory=dict)
    unknown_risk: RiskAssessment | None = None


class Snapshot(Model):
    api_version: Literal['service-scaffold-v1'] = 'service-scaffold-v1'
    session_id: str
    session_epoch: str
    state_version: int
    next_message_id: int
    policy_version: str
    verdict: Verdict


class AnalyzeResponse(Snapshot):
    entities: tuple[Entity, ...]


class MessageSummary(Model):
    message_id: int
    session_alert: bool
    annotation_uncertain: bool
    clarification_reason: Literal['insufficient_statistics'] | None
    coverage_complete: bool


class SessionDetail(Snapshot):
    message_history: tuple[MessageSummary, ...]
