"""Swap quote + unsigned swap tx via Jupiter (Metis Swap API v1), with an integrator fee to the operator's own
token accounts and a safety check of the output token. The fee is taken by Jupiter's program on-chain and lands
directly in FEE_OWNER's USDC or wSOL associated token account (no referral program needed since Jan 2025)."""
import asyncio, os
import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from solders.pubkey import Pubkey
from app.main import app, B58, analyze

JUP = os.getenv("JUP_SWAP_API", "https://api.jup.ag/swap/v1")
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"
FEE_OWNER = os.getenv("SWAP_FEE_OWNER", os.getenv("PAY_TO_ADDRESS", "J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d"))
FEE_BPS = int(os.getenv("SWAP_FEE_BPS", "50"))
_TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
_ATA = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

def fee_account(mint: str) -> str:
    return str(Pubkey.find_program_address([bytes(Pubkey.from_string(FEE_OWNER)), bytes(_TOKEN), bytes(Pubkey.from_string(mint))], _ATA)[0])

def fee_plan(inp: str, out: str):
    """Fee is collected in USDC if either side is USDC, else in SOL; other pairs carry no fee."""
    for m in (USDC, WSOL):
        if m in (inp, out): return m, FEE_BPS
    return None, 0

async def _safety(mint):
    if mint in (USDC, WSOL): return {"risk_score": 0, "risk_level": "low", "flags": []}
    try:
        r = await analyze(mint)
        return {"risk_score": r["risk_score"], "risk_level": r["risk_level"], "flags": [f["code"] for f in r["flags"]]}
    except Exception as e:
        return {"error": str(getattr(e, "detail", e))[:200]}

async def _quote(c, inp, out, amount, slippage_bps):
    for v in (inp, out):
        if not B58.match(v): raise HTTPException(400, "invalid mint")
    if amount <= 0: raise HTTPException(400, "amount must be > 0 (base units)")
    fee_mint, bps = fee_plan(inp, out)
    p = {"inputMint": inp, "outputMint": out, "amount": str(amount), "slippageBps": slippage_bps, "restrictIntermediateTokens": "true"}
    if bps: p["platformFeeBps"] = bps
    r = await c.get(f"{JUP}/quote", params=p)
    if r.status_code != 200: raise HTTPException(502 if r.status_code >= 500 else 400, f"quote failed: {r.text[:300]}")
    return r.json(), fee_mint, bps

def _summary(q, fee_mint, bps):
    return {"in_amount": q["inAmount"], "out_amount": q["outAmount"], "min_out_amount": q["otherAmountThreshold"],
            "price_impact_pct": q.get("priceImpactPct"), "route": [h["swapInfo"].get("label") for h in q.get("routePlan", [])],
            "integrator_fee": {"bps": bps, "mint": fee_mint, "amount": (str(int(q["inAmount"]) * bps // 10000) if fee_mint == q["inputMint"]
                                                                      else (q.get("platformFee") or {}).get("amount")) if bps else "0"}}

@app.get("/v1/swap/quote")
async def swap_quote(inputMint: str, outputMint: str, amount: int, slippageBps: int = 50):
    async with httpx.AsyncClient(timeout=20) as c:
        (q, fee_mint, bps), safety = await asyncio.gather(_quote(c, inputMint, outputMint, amount, slippageBps), _safety(outputMint))
    return {**_summary(q, fee_mint, bps), "output_token_safety": safety, "quote": q,
            "next": "POST /v1/swap/build with {quote, user} to get an unsigned transaction"}

class BuildIn(BaseModel):
    user: str                       # wallet that will sign
    quote: dict | None = None       # 'quote' from /v1/swap/quote (else pass the fields below)
    inputMint: str | None = None
    outputMint: str | None = None
    amount: int | None = None
    slippageBps: int = 50

@app.post("/v1/swap/build")
async def swap_build(b: BuildIn):
    if not B58.match(b.user): raise HTTPException(400, "invalid user")
    async with httpx.AsyncClient(timeout=30) as c:
        if b.quote:
            q = b.quote; fee_mint, bps = fee_plan(q.get("inputMint", ""), q.get("outputMint", ""))
            if bps and (q.get("platformFee") or {}).get("feeBps") != bps:
                raise HTTPException(400, "quote must come from /v1/swap/quote (fee mismatch)")
        else:
            if not (b.inputMint and b.outputMint and b.amount): raise HTTPException(400, "pass quote or inputMint/outputMint/amount")
            q, fee_mint, bps = await _quote(c, b.inputMint, b.outputMint, b.amount, b.slippageBps)
        body = {"quoteResponse": q, "userPublicKey": b.user, "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto", "wrapAndUnwrapSol": True}
        if bps: body["feeAccount"] = fee_account(fee_mint)
        r, safety = await asyncio.gather(c.post(f"{JUP}/swap", json=body), _safety(q["outputMint"]))
    if r.status_code != 200: raise HTTPException(502 if r.status_code >= 500 else 400, f"swap build failed: {r.text[:300]}")
    s = r.json()
    return {**_summary(q, fee_mint, bps), "output_token_safety": safety, "transaction": s["swapTransaction"],
            "last_valid_block_height": s.get("lastValidBlockHeight"),
            "note": "Unsigned v0 transaction: sign with `user` and send. Tip: run POST /v1/tx/preflight first."}
