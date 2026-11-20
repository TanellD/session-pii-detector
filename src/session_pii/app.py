"""FastAPI-приложение. Подключение компонентов выполняется в create_app."""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .components import StubLinker, StubNER, StubRegexp
from .models import AnalyzeRequest, AnalyzeResponse, CreateSession, ResetRequest, SessionDetail, Snapshot
from .pipeline import Pipeline
from .risk import RiskEvaluator
from .service import ServiceError, SessionService, Settings


def create_app(*, ner=None, regexp=None, linker=None, evaluator: RiskEvaluator | None = None,
               settings: Settings | None = None):
    # Замените только эти объекты собственными реализациями Protocol.
    pipeline = Pipeline(ner if ner is not None else StubNER(),
                        regexp if regexp is not None else StubRegexp(),
                        linker if linker is not None else StubLinker(), evaluator)
    service = SessionService(pipeline,settings)
    app = FastAPI(title='Session PII scaffold',version='0.1.0',
                  description='Каркас со стадиями-заглушками. Текущая сборка не является готовым детектором.')
    app.state.service = service

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
        return {'status':'ok','mode':'scaffold','policy_version':pipeline.policy_version}

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
        return service.analyze(payload)

    return app


app = create_app()
