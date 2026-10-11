"""MCP (stdio) wrapper for the Solana Agent Toolbox. Each tool call pays $0.005 USDC via x402 from YOUR wallet.

Env:
  SOLANA_PRIVATE_KEY   base58 secret key of a small, dedicated spending wallet (holds a little USDC; no SOL needed,
                       the facilitator pays fees). Never use a main wallet.
  TOOLBOX_URL          default https://solana-agent-toolbox.onrender.com
  MAX_PRICE_ATOMIC     per-call cap in USDC base units (default 10000 = $0.01); higher quotes are refused.
"""
import os
from mcp.server.mcpserver import MCPServer
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.svm.exact import register_exact_svm_client
from x402.mechanisms.svm.signers import KeypairSigner

URL = os.getenv("TOOLBOX_URL", "https://solana-agent-toolbox.onrender.com").rstrip("/")
MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

server = MCPServer(name="solana-agent-toolbox", version="1.0.0",
                   instructions="Read-only Solana tools (token risk, tx-failure explainer, wallet snapshot). "
                                "Each call costs $0.005 USDC via x402. Output is informational, not financial advice.")

def _client():
    key = os.getenv("SOLANA_PRIVATE_KEY")
    if not key: raise RuntimeError("Set SOLANA_PRIVATE_KEY (base58) for a small dedicated USDC spending wallet")
    c = x402Client()
    cap = os.getenv("MAX_PRICE_ATOMIC", "10000")
    c.set_spend_controls({"max_amount_per_payment": False,
                          "allowed_assets": [{"network": MAINNET, "asset": USDC, "max_amount_per_payment": cap}]})
    register_exact_svm_client(c, KeypairSigner.from_base58(key), networks=[MAINNET])
    return c

async def _call(method, path, body=None):
    async with x402HttpxClient(_client(), base_url=URL, timeout=90) as h:
        r = await h.request(method, path, json=body)
        try: data = r.json()
        except Exception: data = {"text": r.text[:2000]}
        if r.status_code >= 400: return {"error": r.status_code, "detail": data}
        return data

@server.tool()
async def token_safety(mint: str) -> dict:
    """Risk score (0-100), flags, mint/freeze authority, Token-2022 traps, top-holder concentration and DEX pool share for a Solana token mint."""
    return await _call("GET", f"/v1/token/{mint}")

@server.tool()
async def explain_transaction(signature: str) -> dict:
    """Explain why a landed Solana transaction failed: decoded error, failing instruction/program, suggested fix, logs."""
    return await _call("GET", f"/v1/tx/{signature}")

@server.tool()
async def simulate_transaction(transaction_base64: str) -> dict:
    """Simulate a base64-serialized Solana transaction and explain the failure before sending."""
    return await _call("POST", "/v1/tx/simulate", {"transaction": transaction_base64})

@server.tool()
async def wallet_snapshot(address: str) -> dict:
    """Solana wallet snapshot: SOL balance, token holdings, frozen/closable accounts, recent activity."""
    return await _call("GET", f"/v1/wallet/{address}")

@server.tool()
async def tx_preflight(transaction_base64: str, owner: str | None = None) -> dict:
    """Check a Solana transaction BEFORE signing: simulated SOL/token balance changes for the owner, drainer warnings
    (token approve/delegate, set-authority, system assign), unknown programs, and decoded error if it would fail."""
    return await _call("POST", "/v1/tx/preflight", {"transaction": transaction_base64, "owner": owner})

@server.tool()
async def wallet_activity(address: str, limit: int = 10) -> dict:
    """Recent Solana wallet transactions decoded as swap/send/receive with SOL and token balance changes and net flows."""
    return await _call("GET", f"/v1/activity/{address}?limit={limit}")

@server.tool()
async def new_token_launches(limit: int = 10, with_safety: bool = False) -> dict:
    """Latest pump.fun token launches read on-chain (mint, creator, dev buy, creator SOL spent); optional safety score each."""
    return await _call("GET", f"/v1/launches?limit={limit}&with_safety={str(with_safety).lower()}")

def main():
    server.run()

if __name__ == "__main__":
    main()
