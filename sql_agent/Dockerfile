FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
COPY sql_agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY sql_agent /app/sql_agent
COPY docs/answers /app/docs/answers
RUN useradd --create-home agent && mkdir -p /app/runtime && chown agent:agent /app/runtime
USER agent
ENV AGENT_RUNTIME_DIR=/app/runtime PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "sql_agent.web:app", "--host", "0.0.0.0", "--port", "8000"]
