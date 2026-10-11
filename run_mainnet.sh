#!/bin/sh
# Mainnet x402 config. Receive-only payTo; PayAI facilitator pays tx fees. No keys on this box.
export X402_NETWORK=solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp
export PAY_TO_ADDRESS=J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d
export FACILITATOR_URL=https://facilitator.payai.network
export PRICE_ATOMIC=5000
unset FACILITATOR_KEYPAIR PAY_ASSET PAYMENT_RPC_URL
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
