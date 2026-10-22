"""Solana Token Safety API - computes risk purely from raw on-chain RPC data."""
import os, re, time, asyncio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "120"))
PROXY_SECRET = os.getenv("RAPIDAPI_PROXY_SECRET")  # if set, require X-RapidAPI-Proxy-Secret
B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
AMM_PROGRAMS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora DAMM",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun bonding curve",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap",
}
KNOWN_POOL_OWNERS = {"5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Raydium AMM v4"}

app = FastAPI(title="Solana Token Safety API", version="1.0.0",
              description="Risk score for any SPL / Token-2022 mint, computed from raw Solana on-chain data.")
_cache: dict = {}

class RpcError(Exception): pass

async def rpc(client, method, params):
    for attempt in range(3):
        try:
            r = await client.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if r.status_code == 429:
                await asyncio.sleep(1.5 * (attempt + 1)); continue
            r.raise_for_status()
            j = r.json()
            if "error" in j:
                if "WrongSize" in str(j["error"]) or "Invalid param" in str(j["error"]): raise HTTPException(400, "invalid mint address")
                raise RpcError(j["error"].get("message", str(j["error"])))
            return j["result"]
        except httpx.HTTPError as e:
            if attempt == 2: raise RpcError(str(e))
            await asyncio.sleep(1)
    raise RpcError("rate limited by RPC")

def score(flags):
    w = {"high": 30, "medium": 12, "low": 4}
    s = min(100, sum(w[f["severity"]] for f in flags))
    return s, ("high" if s >= 50 else "medium" if s >= 20 else "low")

async def analyze(mint):
    async with httpx.AsyncClient(timeout=20) as c:
        acct = await rpc(c, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        v = acct and acct.get("value")
        if not v or v.get("owner") not in (TOKEN, TOKEN22) or v["data"].get("parsed", {}).get("type") != "mint":
            raise HTTPException(404, "not an SPL token mint")
        info = v["data"]["parsed"]["info"]
        program = "token-2022" if v["owner"] == TOKEN22 else "spl-token"
        dec = info["decimals"]; supply = int(info["supply"])
        exts = [e.get("extension") for e in info.get("extensions", [])]
        conc_ok = True
        try:
            largest = (await rpc(c, "getTokenLargestAccounts", [mint]))["value"]
        except RpcError:
            largest, conc_ok = [], False
        addrs = [a["address"] for a in largest]
        owners, owner_prog = {}, {}
        if addrs:
            ta = (await rpc(c, "getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}]))["value"]
            for a, t in zip(addrs, ta):
                if t: owners[a] = t["data"]["parsed"]["info"]["owner"]
            uniq = list(set(owners.values()))
            oa = (await rpc(c, "getMultipleAccounts", [uniq, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}]))["value"]
            owner_prog = {o: (x["owner"] if x else None) for o, x in zip(uniq, oa)}
    holders, pools = [], []
    for a in largest:
        amt = int(a["amount"]); o = owners.get(a["address"])
        pct = round(100 * amt / supply, 4) if supply else 0
        venue = KNOWN_POOL_OWNERS.get(o) or AMM_PROGRAMS.get(owner_prog.get(o))
        row = {"token_account": a["address"], "owner": o, "ui_amount": amt / 10**dec, "pct_supply": pct}
        (pools if venue else holders).append({**row, "venue": venue} if venue else row)
    top1 = holders[0]["pct_supply"] if holders else 0
    top10 = round(sum(h["pct_supply"] for h in holders[:10]), 4)
    pool_pct = round(sum(p["pct_supply"] for p in pools), 4)
    flags = []
    def f(code, sev, msg): flags.append({"code": code, "severity": sev, "message": msg})
    if info.get("mintAuthority"): f("MINT_AUTHORITY_ACTIVE", "high", "Supply can still be inflated")
    if info.get("freezeAuthority"): f("FREEZE_AUTHORITY_ACTIVE", "high", "Holder accounts can be frozen")
    if top1 > 20: f("TOP_HOLDER_GT_20PCT", "high", f"Largest non-pool holder owns {top1}%")
    elif top1 > 10: f("TOP_HOLDER_GT_10PCT", "medium", f"Largest non-pool holder owns {top1}%")
    if top10 > 50: f("TOP10_GT_50PCT", "medium", f"Top-10 non-pool holders own {top10}%")
    if not conc_ok: f("CONCENTRATION_UNAVAILABLE", "low", "RPC refused getTokenLargestAccounts; holder/pool data omitted")
    elif not pools: f("NO_KNOWN_POOL_IN_TOP20", "medium", "No recognised DEX pool among the 20 largest accounts")
    for e in ("permanentDelegate", "transferHook", "nonTransferable", "defaultAccountState", "pausableConfig"):
        if e in exts: f(f"TOKEN2022_{e.upper()}", "high", f"Token-2022 extension {e} present")
    if "transferFeeConfig" in exts: f("TOKEN2022_TRANSFER_FEE", "medium", "Transfer fee extension present")
    if supply == 0: f("ZERO_SUPPLY", "medium", "Supply is zero")
    s, lvl = score(flags)
    return {"mint": mint, "risk_score": s, "risk_level": lvl, "flags": flags,
            "token": {"program": program, "decimals": dec, "supply": supply / 10**dec, "extensions": exts},
            "authorities": {"mint_authority": info.get("mintAuthority"), "freeze_authority": info.get("freezeAuthority")},
            "concentration": None if not conc_ok else {"top1_pct": top1, "top10_pct": top10, "top_holders": holders[:10],
                              "note": "Excludes recognised DEX pool accounts; based on 20 largest token accounts"},
            "liquidity": None if not conc_ok else {"pool_token_pct_supply": pool_pct, "pools": pools,
                          "note": "Token-side balance held in recognised on-chain DEX pools (no USD price; no third-party data)"},
            "source": "solana-rpc", "computed_at": int(time.time())}

@app.middleware("http")
async def proxy_guard(request: Request, call_next):
    if PROXY_SECRET and request.url.path.startswith("/v1/") and request.headers.get("X-RapidAPI-Proxy-Secret") != PROXY_SECRET:
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)

@app.get("/health")
async def health(): return {"status": "ok", "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7] or None}

@app.get("/v1/token/{mint}")
async def token(mint: str):
    if not B58.match(mint): raise HTTPException(400, "invalid base58 mint address")
    hit = _cache.get(mint)
    if hit and time.time() - hit[0] < CACHE_TTL: return {**hit[1], "cached": True}
    try: res = await analyze(mint)
    except RpcError as e: raise HTTPException(502, f"upstream RPC error: {e}")
    _cache[mint] = (time.time(), res)
    if len(_cache) > 5000: _cache.pop(next(iter(_cache)))
    return {**res, "cached": False}


# ---------------- Transaction-error explainer ----------------
from pydantic import BaseModel
from app.errors import explain_error

class TxIn(BaseModel):
    transaction: str  # base64-encoded serialized (signed or unsigned) transaction

def _programs_from_logs(logs):
    seen = []
    for l in logs or []:
        m = re.match(r"Program (\w+) invoke", l)
        if m and m.group(1) not in seen: seen.append(m.group(1))
    return seen

def _explain(err, logs, units=None):
    out = {"success": err is None, "error_raw": err, "programs_invoked": _programs_from_logs(logs),
           "compute_units_consumed": units, "logs": (logs or [])[-40:]}
    out.update(explain_error(err, logs or []))
    return out

@app.post("/v1/tx/simulate")
async def tx_simulate(body: TxIn):
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            r = await rpc(c, "simulateTransaction", [body.transaction, {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True}])
        except RpcError as e:
            if "failed to deserialize" in str(e).lower() or "invalid" in str(e).lower():
                raise HTTPException(400, f"could not decode transaction: {e}")
            raise HTTPException(502, f"upstream RPC error: {e}")
    v = r["value"]
    return _explain(v.get("err"), v.get("logs"), v.get("unitsConsumed"))

@app.get("/v1/tx/{signature}")
async def tx_explain(signature: str):
    if not re.match(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$", signature): raise HTTPException(400, "invalid signature")
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            t = await rpc(c, "getTransaction", [signature, {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
        except RpcError as e:
            raise HTTPException(502, f"upstream RPC error: {e}")
    if not t: raise HTTPException(404, "transaction not found")
    m = t["meta"]
    return {"signature": signature, "slot": t["slot"], "block_time": t.get("blockTime"), "fee_lamports": m["fee"],
            **_explain(m.get("err"), m.get("logMessages"), m.get("computeUnitsConsumed"))}

# ---------------- Wallet / holder snapshot ----------------
@app.get("/v1/wallet/{address}")
async def wallet(address: str, limit: int = 25):
    if not B58.match(address): raise HTTPException(400, "invalid base58 address")
    limit = max(1, min(limit, 100))
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            bal = await rpc(c, "getBalance", [address])
            accts = []
            for prog in (TOKEN, TOKEN22):
                r = await rpc(c, "getTokenAccountsByOwner", [address, {"programId": prog}, {"encoding": "jsonParsed"}])
                for a in r["value"]:
                    i = a["account"]["data"]["parsed"]["info"]; ta = i["tokenAmount"]
                    accts.append({"token_account": a["pubkey"], "mint": i["mint"], "program": "token-2022" if prog == TOKEN22 else "spl-token",
                                  "ui_amount": float(ta.get("uiAmountString") or 0), "decimals": ta["decimals"], "frozen": i.get("state") == "frozen"})
            sigs = await rpc(c, "getSignaturesForAddress", [address, {"limit": 10}])
        except RpcError as e:
            raise HTTPException(502, f"upstream RPC error: {e}")
    nonzero = sorted([a for a in accts if a["ui_amount"] > 0], key=lambda a: -a["ui_amount"])
    return {"address": address, "sol": bal["value"] / 1e9, "token_accounts_total": len(accts),
            "token_accounts_nonzero": len(nonzero), "empty_accounts_closable": len(accts) - len(nonzero),
            "frozen_accounts": sum(a["frozen"] for a in accts), "tokens": nonzero[:limit],
            "recent_activity": [{"signature": s["signature"], "block_time": s.get("blockTime"), "failed": s.get("err") is not None} for s in sigs],
            "note": "Balances are raw token amounts (no USD pricing, no third-party data)"}

from app import extra  # noqa: E402,F401  (new paid endpoints)
from app import swap  # noqa: E402,F401

from pathlib import Path as _Path
from fastapi.responses import PlainTextResponse as _PlainText

_LLMS = _Path(__file__).resolve().parent.parent / "llms.txt"

@app.get("/llms.txt", response_class=_PlainText, include_in_schema=False)
async def llms_txt():
    """Free: agent-readable summary, base URL rewritten to PUBLIC_BASE_URL."""
    base = os.getenv("PUBLIC_BASE_URL", "https://solana-agent-toolbox.onrender.com").rstrip("/")
    return _LLMS.read_text().replace("https://solana-agent-toolbox.onrender.com", base)


# ---------------- x402 payments (must be imported last) ----------------
from app import payments  # noqa: E402,F401
payments.install(app)
