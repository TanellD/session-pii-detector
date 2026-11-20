"""Ограниченное состояние в памяти, порядок, epoch, CAS и квитанция последнего запроса."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from threading import RLock
import time
from uuid import uuid4

from .components import Fact
from .models import AnalyzeRequest, AnalyzeResponse, MessageSummary, SessionDetail, Snapshot, Verdict
from .pipeline import Pipeline


class ServiceError(Exception):
    def __init__(self, status: int, code: str, state: Snapshot | None = None):
        self.status, self.code, self.state = status, code, state


@dataclass(frozen=True)
class Settings:
    ttl_seconds: int = 1800
    max_sessions: int = 1000
    max_messages: int = 1000
    max_entities: int = 1000

    def __post_init__(self):
        if any(type(v) is not int or v<=0 for v in vars(self).values()):
            raise ValueError('Service limits must be positive integers')

    @classmethod
    def from_env(cls):
        return cls(**{name:int(os.environ.get('PII_'+name.upper(),default))
                      for name,default in vars(cls()).items()})


@dataclass
class Session:
    session_id: str
    expires_at: float
    epoch: str = field(default_factory=lambda:uuid4().hex)
    version: int = 0
    facts: tuple[Fact, ...] = ()
    verdict: Verdict = field(default_factory=Verdict)
    last_fingerprint: str | None = None
    last_response: AnalyzeResponse | None = None
    message_history: tuple[MessageSummary, ...] = ()
    lock: RLock = field(default_factory=RLock)


class SessionService:
    def __init__(self, pipeline: Pipeline, settings: Settings | None = None, clock=time.monotonic):
        self.pipeline = pipeline
        self.settings = settings or Settings.from_env()
        self.clock = clock
        self.sessions: dict[str,Session] = {}
        self.directory_lock = RLock()

    def _snapshot(self, entry):
        return Snapshot(session_id=entry.session_id,session_epoch=entry.epoch,state_version=entry.version,
                        next_message_id=entry.version+1,policy_version=self.pipeline.policy_version,
                        verdict=entry.verdict)

    def _entry(self, session_id, status=410):
        with self.directory_lock:
            entry = self.sessions.get(session_id)
        if entry is None:
            raise ServiceError(status,'SESSION_NOT_FOUND' if status==404 else 'SESSION_EXPIRED')
        return entry

    def _alive(self, entry, status=410):
        if self.clock()>=entry.expires_at:
            raise ServiceError(status,'SESSION_NOT_FOUND' if status==404 else 'SESSION_EXPIRED')

    def create(self, session_id):
        with self.directory_lock:
            for key,entry in list(self.sessions.items()):
                if entry.lock.acquire(blocking=False):
                    try:
                        if self.clock()>=entry.expires_at:
                            del self.sessions[key]
                    finally:
                        entry.lock.release()
            if session_id in self.sessions:
                with self.sessions[session_id].lock:
                    raise ServiceError(409,'SESSION_EXISTS',self._snapshot(self.sessions[session_id]))
            if len(self.sessions)>=self.settings.max_sessions:
                raise ServiceError(503,'SESSION_CAPACITY_REACHED')
            entry = Session(session_id,self.clock()+self.settings.ttl_seconds)
            self.sessions[session_id] = entry
            return self._snapshot(entry)

    def get(self, session_id):
        entry = self._entry(session_id,404)
        with entry.lock:
            self._alive(entry,404)
            # GET не продлевает TTL; история не содержит исходных текстов.
            return SessionDetail(**self._snapshot(entry).model_dump(),message_history=entry.message_history)

    def reset(self, session_id, epoch, version):
        entry = self._entry(session_id)
        with entry.lock:
            self._alive(entry)
            self._check(entry,epoch,version)
            entry.epoch,entry.version,entry.facts,entry.verdict = uuid4().hex,0,(),Verdict()
            entry.last_fingerprint,entry.last_response = None,None
            entry.message_history = ()
            entry.expires_at = self.clock()+self.settings.ttl_seconds
            return self._snapshot(entry)

    def _check(self, entry, epoch, version):
        if entry.epoch!=epoch or entry.version!=version:
            raise ServiceError(409,'STATE_CONFLICT',self._snapshot(entry))

    def analyze(self, request: AnalyzeRequest):
        entry = self._entry(request.session_id)
        fingerprint = sha256(json.dumps(request.model_dump(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with entry.lock:
            self._alive(entry)
            # Точный retry проверяется до CAS и не вызывает детекторы повторно.
            if entry.last_fingerprint==fingerprint:
                return entry.last_response
            self._check(entry,request.session_epoch,request.expected_state_version)
            if request.message.message_id!=entry.version+1:
                raise ServiceError(409,'OUT_OF_ORDER',self._snapshot(entry))
            if entry.version>=self.settings.max_messages:
                raise ServiceError(413,'MESSAGE_LIMIT_REACHED',self._snapshot(entry))
            try:
                step = self.pipeline.process(request.message,entry.facts,entry.verdict)
            except Exception:
                # Ошибка компонента может содержать исходный текст: наружу не передаём.
                raise ServiceError(502,'PIPELINE_FAILURE',self._snapshot(entry)) from None
            if len(step.facts)>self.settings.max_entities:
                raise ServiceError(413,'ENTITY_LIMIT_REACHED',self._snapshot(entry))
            # Коммит после успешного выполнения всех стадий и проверок.
            response = AnalyzeResponse(session_id=entry.session_id,session_epoch=entry.epoch,
                                       state_version=entry.version+1,next_message_id=entry.version+2,
                                       policy_version=self.pipeline.policy_version,
                                       verdict=step.verdict,entities=step.entities)
            summary = MessageSummary(message_id=request.message.message_id,
                                     session_alert=step.verdict.session_alert,
                                     annotation_uncertain=step.verdict.annotation_uncertain,
                                     clarification_reason=step.verdict.clarification_reason,
                                     coverage_complete=step.verdict.coverage_complete)
            entry.version += 1
            entry.facts,entry.verdict = step.facts,step.verdict
            entry.last_fingerprint,entry.last_response = fingerprint,response
            entry.message_history += (summary,)
            entry.expires_at = self.clock()+self.settings.ttl_seconds
            return response
