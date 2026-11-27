FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/tmp/huggingface \
    PIP_DEFAULT_TIMEOUT=60
WORKDIR /app

COPY requirements-service.lock.txt ./
RUN pip install --no-cache-dir -r requirements-service.lock.txt \
    && useradd --uid 10001 --create-home appuser

COPY src/session_pii ./src/session_pii
COPY configs/annotation-estimator-policy.json ./configs/annotation-estimator-policy.json
COPY configs/runtime-risk-policy.json ./configs/runtime-risk-policy.json
COPY configs/runtime-risk-tuned-validation.json configs/linker-gemma-baseline.json ./configs/
COPY benchmarks/adapter/__init__.py benchmarks/adapter/adapter.py benchmarks/adapter/adapter_contract.py benchmarks/adapter/adapter_rules.py benchmarks/adapter/_rules_impl.py ./benchmarks/adapter/
COPY benchmarks/pii_smoke/config.json benchmarks/pii_smoke/regex_detector.py benchmarks/pii_smoke/phone_format.py benchmarks/pii_smoke/prepare.py ./benchmarks/pii_smoke/
COPY tools/annotation_calculator.py tools/annotation_contract.py tools/ownership.py tools/estimate_occupation_candidates.py ./tools/

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2)"
CMD ["python", "-m", "uvicorn", "session_pii.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM runtime AS gliner
USER root
COPY requirements-gliner-cpu.txt ./
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.11.0 \
    && pip install --no-cache-dir -r requirements-gliner-cpu.txt
USER 10001

FROM runtime AS default
