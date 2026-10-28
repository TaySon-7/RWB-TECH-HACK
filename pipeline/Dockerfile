FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
COPY pipeline/requirements.txt /app/pipeline/requirements.txt
RUN pip install --no-cache-dir -r pipeline/requirements.txt
COPY pipeline /app/pipeline
COPY schemas /app/schemas
COPY clickhouse /app/clickhouse
RUN python -m grpc_tools.protoc -I schemas --python_out=pipeline schemas/marketplace.proto
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "pipeline/manage.py"]
