FROM python:3.14-slim

WORKDIR /app
COPY main.py /app/main.py

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

USER 10001:10001

EXPOSE 8080
ENTRYPOINT ["python", "-u", "/app/main.py"]
