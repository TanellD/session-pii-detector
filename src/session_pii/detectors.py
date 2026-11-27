"""Подключение общего адаптера: regex или явно выбранная локальная GLiNER."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from threading import Lock

from benchmarks.adapter.adapter import extract
from .components import DetectionResult, EmptyDetector, StubNER, StubRegexp
from .models import MentionOrigin, Span
from .observability import emit

ROOT = Path(__file__).resolve().parents[2]
TYPE_MAP = {
    'PERSON': 'person', 'FULL_NAME': 'person',
    'FIRST_NAME': 'first_name', 'LAST_NAME': 'last_name', 'PATRONYMIC': 'patronymic',
    'GENDER': 'sex', 'AGE': 'age', 'CITY': 'geography', 'ADDRESS': 'address',
    'ORGANIZATION': 'organization', 'OCCUPATION': 'occupation',
    'PHONE': 'phone', 'EMAIL': 'email', 'PASSPORT': 'passport', 'SNILS': 'snils',
    'INN': 'individual_inn', 'CARD': 'bank_card',
}


@dataclass(frozen=True)
class DetectorSettings:
    mode: str = 'regex'
    model_dir: Path | None = None
    config_path: Path = ROOT / 'benchmarks/pii_smoke/config.json'
    adapter_revision: str = 'base'

    def __post_init__(self):
        if self.mode not in {'regex', 'gliner', 'stub'}:
            raise ValueError('PII_DETECTOR_MODE must be regex, gliner or stub')
        if self.mode == 'gliner' and self.model_dir is None:
            raise ValueError('PII_MODEL_DIR is required for local GLiNER')
        if self.adapter_revision not in {'base', 'v2'}:
            raise ValueError('PII_ADAPTER_REVISION must be base or v2')

    @classmethod
    def from_env(cls):
        path = os.environ.get('PII_MODEL_DIR')
        return cls(mode=os.environ.get('PII_DETECTOR_MODE', 'regex'),
                   model_dir=Path(path) if path else None,
                   adapter_revision=os.environ.get('PII_ADAPTER_REVISION', 'v2'))


class AdapterDetector:
    def __init__(self, settings: DetectorSettings | None = None, *, predictor=None):
        self.settings = settings or DetectorSettings()
        if self.settings.mode == 'stub':
            raise ValueError('Use stub components for stub mode')
        self.config = json.loads(self.settings.config_path.read_text(encoding='utf-8'))
        self.predictor = predictor
        self.model = None
        self.lock = Lock()

    def initialize(self):
        if self.settings.mode == 'gliner':
            emit('model_warmup_start')
            import torch
            torch.set_num_threads(int(os.environ.get('PII_TORCH_THREADS', str(self.config.get('threads', 2)))))
            self._predict('Проверка готовности модели.')
            emit('model_warmup_complete')

    def _predict(self, text):
        # Один экземпляр модели и сериализация её вызовов; состояния сессий здесь нет.
        with self.lock:
            if self.predictor is not None:
                return self.predictor(text)
            if self.model is None:
                emit('model_load_start')
                from benchmarks.pii_smoke.prepare import read_manifest
                read_manifest(self.settings.model_dir, self.config)
                from gliner import GLiNER
                self.model = GLiNER.from_pretrained(
                    str(self.settings.model_dir.resolve()), local_files_only=True,
                    load_tokenizer=True, token=False, strict=True).to('cpu').eval()
                emit('model_load_complete')
            return self.model.predict_entities(text, list(self.config['labels']),
                        threshold=self.config['threshold'], flat_ner=True)

    def detect(self, message):
        raw = self._predict(message.text) if self.settings.mode == 'gliner' else []
        mentions = extract(message.text, gliner_entities=raw, config=self.config)
        spans = []
        unsupported = False
        for mention in mentions:
            kind = TYPE_MAP.get(mention.type)
            if kind is None:
                unsupported = True
                continue
            origin = MentionOrigin(mention_id=mention.id, source=mention.source,
                                   detector_version=mention.detector_version,
                                   confidence=mention.confidence,
                                   confidence_kind=mention.confidence_kind)
            spans.append(Span(type=kind, start=mention.start, end=mention.end,
                              score=mention.confidence, provenance=(origin,)))
        reasons = []
        if self.settings.mode == 'regex':
            reasons.append('ner_not_configured')
        if unsupported:
            reasons.append('unsupported_detector_type')
        return DetectionResult(tuple(spans), complete=not reasons, reasons=tuple(reasons))


def build_detectors(settings):
    if settings.mode == 'stub':
        return StubNER(), StubRegexp()
    # extract() уже объединяет модель и regex: второй regex не вызывается.
    base = AdapterDetector(settings)
    if settings.adapter_revision == 'v2':
        from .adapter_revision_v2 import RevisedAdapter
        detector = RevisedAdapter(base)
        detector.initialize = base.initialize
        return detector, EmptyDetector()
    return base, EmptyDetector()
