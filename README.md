# Solana Agent Toolbox: x402 pay-per-call (USDC on Solana)

> **Disclaimer:** All output is informational only and is **not financial, investment or legal advice**. Risk scores are heuristics computed from public on-chain data and can be wrong; a "low" score does not mean a token is safe. Use at your own risk.

Read-only Solana tools for AI agents. Every paid call costs **$0.005 USDC** (configurable) and is paid with
[x402](https://docs.x402.org): an unpaid request gets `HTTP 402` with a `PAYMENT-REQUIRED` header (x402 v2,
scheme `exact`, Solana). Any x402 client signs a USDC transfer to `PAY_TO_ADDRESS` and retries. The money lands
straight in that wallet. No accounts, no API keys.

| Route | What it returns |
|---|---|
| `GET /v1/token/{mint}` | Risk score 0-100, flags, mint/freeze authority, Token-2022 traps, top-holder %, DEX pool share |
| `GET /v1/tx/{signature}` | Why a landed tx failed: decoded error, failing instruction/program, suggested fix, logs |
| `POST /v1/tx/simulate` `{"transaction": "<base64>"}` | Simulate before sending and explain the failure |
| `GET /v1/wallet/{address}` | SOL, token holdings, frozen/closable accounts, recent activity |
| `GET /health`, `GET /.well-known/x402` | Free: health and discovery JSON |

All data comes from raw Solana RPC. No RugCheck/DexScreener/GeckoTerminal data, so there's no resale/licensing problem.
Bazaar discovery metadata (x402 `bazaar` extension: description, tags, input/output examples) is attached to every
paid route, so x402 facilitators that index Bazaar can list it automatically.

## Config (env)
| Var | Default | Notes |
|---|---|---|
| `PAY_TO_ADDRESS` | throwaway devnet address | **Dan's receive-only Solana address** for mainnet |
| `X402_NETWORK` | `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` (devnet) | mainnet: `solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp` |
| `PAY_ASSET` | USDC of the network | |
| `PRICE_ATOMIC` | `5000` | = $0.005 (USDC has 6 decimals) |
| `FACILITATOR_URL` | — | hosted facilitator, e.g. `https://facilitator.payai.network` |
| `FACILITATOR_KEYPAIR` | — | OR self-facilitate: JSON keypair that pays tx fees (needs a little SOL, never receives revenue) |
| `PAYMENT_RPC_URL` | network default | RPC used to verify/settle payments |
| `SOLANA_RPC_URL` | mainnet public | data RPC; a keyed RPC is needed for holder concentration |
| `X402_ENABLED` | `1` | `0` = free mode (dev only) |

## Run / test
    python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
    PYTHONPATH=. .venv/bin/pytest -q                     # unit + 402 tests (LIVE=1 adds mainnet reads)
    # end-to-end payment test on a local validator (devnet RPC was unreachable from the build box):
    solana-test-validator --reset &      # then fund keys in devnet/ and create a test USDC mint (see E2E_EVIDENCE.txt; generate throwaway keys into devnet/, which is git-ignored)
    SOLANA_RPC_URL=http://127.0.0.1:8899 PAYMENT_RPC_URL=http://127.0.0.1:8899 FACILITATOR_KEYPAIR=devnet/feepayer.json \
      PAY_ASSET=<test mint> .venv/bin/uvicorn app.main:app --port 8090 &
    ASSET=<test mint> .venv/bin/python scripts/e2e_local.py
    # on real devnet: RPC=https://api.devnet.solana.com, PAY_ASSET unset (devnet USDC), payer funded from faucets

Output is informational, not financial advice. Keys in `devnet/` are throwaway test keys and must never be used on mainnet.
