FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PORT=8080
# No private keys baked in. For self-facilitation mount a fee-payer keypair and set FACILITATOR_KEYPAIR,
# or set FACILITATOR_URL to a hosted facilitator.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
