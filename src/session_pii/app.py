"""FastAPI-приложение. Подключение компонентов выполняется в create_app."""
from contextlib import asynccontextmanager
import os
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .components import StubNER, StubRegexp
from .detectors import DetectorSettings, build_detectors
from .models import AnalyzeRequest, AnalyzeResponse, CreateSession, ResetRequest, SessionDetail, Snapshot
from .pipeline import Pipeline
from .risk import RiskEvaluator
from .linker_runtime import runtime_linker
from .observability import context, emit, timed
from .service import ServiceError, SessionService, Settings


def create_app(*, ner=None, regexp=None, linker=None, evaluator: RiskEvaluator | None = None,
               settings: Settings | None = None, detector_settings: DetectorSettings | None = None):
    detection = detector_settings or DetectorSettings.from_env()
    if ner is None and regexp is None:
        ner, regexp = build_detectors(detection)
    # Явно переданные компоненты сохраняют прежний интерфейс Protocol.
    pipeline = Pipeline(ner if ner is not None else StubNER(),
                        regexp if regexp is not None else StubRegexp(),
                        linker if linker is not None else runtime_linker(), evaluator)
    service = SessionService(pipeline,settings)
    ready = {'value': False}
    @asynccontextmanager
    async def lifespan(app):
        initialize = getattr(pipeline.ner, 'initialize', None)
        start = time.perf_counter()
        if initialize:
            await run_in_threadpool(initialize)
        ready['value'] = True
        emit('startup', detector_mode=detection.mode, adapter_revision=detection.adapter_revision,
             linker_mode=os.environ.get('PII_LINKER_MODE', 'stub'), policy_version=pipeline.policy_version,
             warmup_ms=round((time.perf_counter()-start)*1000, 3))
        yield
        ready['value'] = False
    app = FastAPI(title='Session PII service',version='0.2.0', lifespan=lifespan,
                  description='Сессионный сервис с адаптером извлечения и заменяемым линкером.')
    app.state.service = service
    max_inflight = int(os.environ.get('PII_MAX_INFLIGHT', '32'))
    if max_inflight <= 0:
        raise ValueError('PII_MAX_INFLIGHT must be positive')
    inflight = 0
    sample_rate = float(os.environ.get('PII_LOG_SAMPLE_RATE', '1'))
    if not 0 <= sample_rate <= 1:
        raise ValueError('PII_LOG_SAMPLE_RATE must be between 0 and 1')

    @app.middleware('http')
    async def telemetry(request, call_next):
        nonlocal inflight
        request_id = uuid4().hex
        state = dict(request_id=request_id, stages_ms={}, sampled=int(request_id[:8], 16)/0xffffffff < sample_rate)
        token = context.set(state)
        start = time.perf_counter()
        admitted = False
        try:
            if inflight >= max_inflight and request.url.path == '/v1/analyze':
                response = JSONResponse(status_code=503, content={'error': {'code': 'OVERLOADED'}, 'state': None})
            else:
                # No await between capacity check and increment on this event loop.
                inflight += 1
                admitted = True
                try:
                    response = await call_next(request)
                except Exception:
                    emit('request_error')
                    response = JSONResponse(status_code=500, content={'error': {'code': 'INTERNAL_ERROR'}, 'state': None})
            response.headers['X-Request-ID'] = request_id
            response.headers['Server-Timing'] = ', '.join(f'{k};dur={v:.3f}' for k, v in state['stages_ms'].items())
            route = getattr(request.scope.get('route'), 'path', 'unmatched')
            stages = {k: round(v, 3) for k, v in state['stages_ms'].items()}
            emit('http_request', method=request.method if request.method in {'GET', 'POST'} else 'other',
                 route=route, status=response.status_code, http_ms=round((time.perf_counter()-start)*1000, 3),
                 stages_ms=stages, analysis_excluding_linker_ms=round(max(0,
                     stages.get('analysis', 0)-stages.get('linker', 0)), 3))
            return response
        finally:
            if admitted:
                inflight -= 1
            context.reset(token)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse(status_code=error.status,
                            content={'error':{'code':error.code},
                                     'state':error.state.model_dump(mode='json') if error.state else None})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Стандартный ответ Pydantic включает input; исходные тексты не отражаем.
        return JSONResponse(status_code=422,content={'error':{'code':'INVALID_REQUEST'},'state':None})

    @app.get('/healthz')
    def health():
        return {'status':'ok','mode':detection.mode,'policy_version':pipeline.policy_version}

    @app.get('/readyz')
    def readiness():
        return JSONResponse(status_code=200 if ready['value'] else 503, content=dict(
            status='ready' if ready['value'] else 'starting', mode=detection.mode,
            adapter_revision=detection.adapter_revision, policy_version=pipeline.policy_version,
            linker_mode=os.environ.get('PII_LINKER_MODE', 'stub'),
            settings=dict(max_inflight=max_inflight, log_sample_rate=sample_rate,
                          linker_fallback=os.environ.get('PII_LINKER_FALLBACK', 'error'),
                          torch_threads=int(os.environ.get('PII_TORCH_THREADS', '2')),
                          linker_concurrency=int(os.environ.get('PII_LINKER_CONCURRENCY', '1')),
                          benchmark_linker_delay_ms=float(os.environ.get('PII_BENCHMARK_LINKER_DELAY_MS', '0')),
                          linker_timeout_seconds=float(os.environ.get('PII_LINKER_TIMEOUT_SECONDS', '90')),
                          session_limits=vars(service.settings))))

    @app.post('/v1/sessions',response_model=Snapshot,status_code=201)
    def create(payload: CreateSession):
        return service.create(payload.session_id)

    @app.get('/v1/sessions/{session_id}',response_model=SessionDetail)
    def snapshot(session_id: str):
        return service.get(session_id)

    @app.post('/v1/sessions/{session_id}/reset',response_model=Snapshot)
    def reset(session_id: str, payload: ResetRequest):
        return service.reset(session_id,payload.session_epoch,payload.expected_state_version)

    @app.post('/v1/analyze',response_model=AnalyzeResponse)
    def analyze(payload: AnalyzeRequest):
        with timed('analysis'):
            result = service.analyze(payload)
        emit('analysis_result', entities=len(result.entities), coverage_complete=result.verdict.coverage_complete,
             alert=result.verdict.session_alert)
        return result

    return app


app = create_app()
