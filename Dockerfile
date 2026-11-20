FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app
WORKDIR /app

COPY requirements-service.lock.txt ./
RUN pip install --no-cache-dir -r requirements-service.lock.txt \
    && useradd --uid 10001 --create-home appuser

COPY src/session_pii ./src/session_pii
COPY configs/annotation-estimator-policy.json ./configs/annotation-estimator-policy.json
COPY configs/runtime-risk-policy.json ./configs/runtime-risk-policy.json
COPY tools/annotation_calculator.py tools/annotation_contract.py tools/estimate_occupation_candidates.py ./tools/

USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2)"
CMD ["python", "-m", "uvicorn", "session_pii.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
