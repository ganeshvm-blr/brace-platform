FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    kubernetes==37.0.1 \
    urllib3==2.8.0

COPY src/ /app/src/
COPY workloads/verifier/verifier.py /app/verifier.py

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

USER 10001:10001

ENTRYPOINT ["python", "/app/verifier.py"]
