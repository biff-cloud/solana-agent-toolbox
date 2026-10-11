# Solana Agent Toolbox: x402 pay-per-call (USDC on Solana)

Live: https://solana-agent-toolbox.onrender.com · Also see [x402 Health](https://github.com/biff-cloud/x402-health) (check any x402 endpoint before paying) · [All services](https://biff-cloud.github.io)

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
| `POST /v1/tx/preflight` | Simulate before signing: owner SOL/token balance changes, drainer warnings, unknown programs |
| `GET /v1/activity/{address}?limit=10` | Decoded recent txs (swap/send/receive), per-tx balance changes, net flows |
| `GET /v1/swap/quote?inputMint&outputMint&amount` | Jupiter quote + output-token safety check |
| `POST /v1/swap/build` (free) | Unsigned Jupiter swap tx; 0.5% integrator fee on pairs containing USDC or SOL, paid on-chain to the operator's token accounts |
| `GET /v1/launches?limit=10&with_safety=true` | New pump.fun launches from chain (mint, creator, dev buy) + optional risk score |
| `GET /health`, `GET /.well-known/x402`, `GET /openapi.json` | Free: health, x402 discovery (x402scan format), OpenAPI with `x-payment-info` |
| `GET /stats` | Free: paid-call counters since restart + on-chain USDC payments ($0.005-sized) to payTo |

All data comes from raw Solana RPC. No RugCheck/DexScreener/GeckoTerminal data, so there's no resale/licensing problem.
Bazaar discovery metadata (x402 `bazaar` extension: description, tags, input/output examples) is attached to every
paid route, so x402 facilitators that index Bazaar can list it automatically.

## Quick start for agents

**1. See the price (free):**
```bash
curl -i "https://solana-agent-toolbox.onrender.com/v1/token/JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"   # HTTP 402 + PAYMENT-REQUIRED header (base64 JSON: network, amount, payTo)
```

**2. Pay and call, TypeScript** (`npm i @x402/fetch @x402/svm @solana/kit @scure/base`):
```ts
import { x402Client, wrapFetchWithPayment } from "@x402/fetch";
import { ExactSvmScheme } from "@x402/svm/exact/client";
import { createKeyPairSignerFromBytes } from "@solana/kit";
import { base58 } from "@scure/base";

const signer = await createKeyPairSignerFromBytes(base58.decode(process.env.SVM_PRIVATE_KEY!)); // small dedicated wallet with a little USDC
const client = new x402Client();
client.register("solana:*", new ExactSvmScheme(signer));
const pay = wrapFetchWithPayment(fetch, client);
console.log(await (await pay("https://solana-agent-toolbox.onrender.com/v1/token/JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN")).json());
```

**3. Pay and call, Python** (`pip install "x402[svm,httpx]"`):
```python
import asyncio, os
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.svm.exact import register_exact_svm_client
from x402.mechanisms.svm.signers import KeypairSigner

c = x402Client()
register_exact_svm_client(c, KeypairSigner.from_base58(os.environ["SOLANA_PRIVATE_KEY"]))
async def main():
    async with x402HttpxClient(c, timeout=90) as h:
        print((await h.get("https://solana-agent-toolbox.onrender.com/v1/token/JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN")).json())
asyncio.run(main())
```

**4. MCP (Claude Desktop `claude_desktop_config.json`, Cursor `~/.cursor/mcp.json`):**
```json
{"mcpServers": {"solana-agent-toolbox": {"command": "uvx",
  "args": ["--from", "git+https://github.com/biff-cloud/solana-agent-toolbox", "solana-agent-toolbox-mcp"],
  "env": {"SOLANA_PRIVATE_KEY": "<base58 key of a SMALL dedicated wallet>", "MAX_PRICE_ATOMIC": "10000"}}}}
```
Payments are USDC on Solana mainnet. The facilitator (PayAI) pays the SOL fees, so the wallet needs only a few cents of USDC. Use a dedicated low-balance wallet, never your main one.

## Config (env)
| Var | Default | Notes |
|---|---|---|
| `PAY_TO_ADDRESS` | throwaway devnet address | receive-only Solana address for mainnet |
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

## Use it from an AI agent (MCP)
A stdio MCP server is included. It pays per tool call from a small dedicated spending wallet (USDC on Solana; no SOL needed, the facilitator pays fees). There is a per-call cap, default $0.01.

    uvx --from git+https://github.com/biff-cloud/solana-agent-toolbox solana-agent-toolbox-mcp

Claude Desktop / Cursor config:

    {"mcpServers": {"solana-agent-toolbox": {"command": "uvx",
      "args": ["--from", "git+https://github.com/biff-cloud/solana-agent-toolbox", "solana-agent-toolbox-mcp"],
      "env": {"SOLANA_PRIVATE_KEY": "<base58 key of a SMALL dedicated wallet>"}}}}

Tools: `token_safety`, `explain_transaction`, `simulate_transaction`, `wallet_snapshot`.

## Revenue check (read-only)
    SOLANA_RPC_URL=... PRICE_ATOMIC=5000 REVENUE_SINCE=<unix ts> python -m app.revenue <payTo>
