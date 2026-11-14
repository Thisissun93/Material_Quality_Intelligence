# One image for the API, the Streamlit app and the init job (they differ only in the command).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements.txt -r requirements-api.txt

COPY mqi ./mqi
COPY api ./api
COPY app ./app
COPY config ./config
COPY .streamlit ./.streamlit

RUN useradd --create-home mqi && mkdir -p /app/data && chown -R mqi /app/data /app/config
USER mqi

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
