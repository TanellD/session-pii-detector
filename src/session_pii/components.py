"""Точки подключения NER, regexp и линкера. Здесь заменять заглушки."""
from dataclasses import dataclass
from typing import Protocol

from .models import Candidate, Message, Span, MentionOrigin, MentionKey


@dataclass(frozen=True)
class Fact:
    type: str
    value: str
    person_id: str | tuple[str, ...] | None
    entity_id: str
    message_id: int
    service_contact: bool = False
    provenance: tuple[MentionOrigin, ...] = ()
    mention_keys: tuple[MentionKey, ...] = ()


@dataclass(frozen=True)
class DetectionResult:
    spans: tuple[Span, ...] = ()
    complete: bool = True
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkResult:
    # Одна запись на Candidate: ID, tuple владельцев либо None.
    person_ids: tuple[str | tuple[str, ...] | None, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()
    # Необязательно: маска служебных phone/email, исключаемых из риска.
    service_contacts: tuple[bool, ...] = ()


class Detector(Protocol):
    def detect(self, message: Message) -> DetectionResult: ...


class Linker(Protocol):
    def link(self, message: Message, candidates: tuple[Candidate, ...],
             known_facts: tuple[Fact, ...]) -> LinkResult: ...


class StubNER:
    def detect(self, message: Message) -> DetectionResult:
        # TODO: вызов своей NER-модели только для message.text.
        return DetectionResult(complete=False, reasons=('ner_not_configured',))


class StubRegexp:
    def detect(self, message: Message) -> DetectionResult:
        # TODO: регулярные выражения; полный тип, исходные границы, score=None.
        return DetectionResult(complete=False, reasons=('regexp_not_configured',))


class StubLinker:
    def link(self, message: Message, candidates: tuple[Candidate, ...],
             known_facts: tuple[Fact, ...]) -> LinkResult:
        # TODO: связать новые спаны с локальными person_id либо оставить None.
        # known_facts неизменяем; прежние неизвестные владельцы не переназначаются.
        return LinkResult(tuple(None for _ in candidates), complete=False,
                          reasons=('linker_not_configured',))


class EmptyDetector:
    """Пустая вторая стадия: объединённый адаптер уже выполнил regex."""
    def detect(self, message: Message) -> DetectionResult:
        return DetectionResult()
