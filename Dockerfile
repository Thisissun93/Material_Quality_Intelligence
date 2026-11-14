# One image for every service: API, Streamlit app, init job, equipment simulator, collector.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements.txt -r requirements-api.txt

COPY mqi ./mqi
COPY api ./api
COPY equipment ./equipment
COPY collector ./collector
COPY app ./app
COPY config ./config
COPY .streamlit ./.streamlit

RUN useradd --create-home mqi && mkdir -p /app/data /app/spool && chown -R mqi /app/data /app/config /app/spool
USER mqi

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
