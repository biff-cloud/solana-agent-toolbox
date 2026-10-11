"""New paid endpoints: tx preflight (simulate before signing), wallet activity, pump.fun launch feed."""
import asyncio, base64, re, time
import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from solders.transaction import VersionedTransaction
from app.main import app, rpc, RpcError, B58, TOKEN, TOKEN22, analyze

SYSTEM = "11111111111111111111111111111111"
LABELS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM v4",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool", "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG": "Meteora DAMM v2", SYSTEM: "System", TOKEN: "SPL Token", TOKEN22: "Token-2022",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated Token", "ComputeBudget111111111111111111111111111111": "Compute Budget",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr": "Memo", "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s": "Metaplex Metadata",
}
PUMP_MINT_AUTHORITY = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"  # signs every pump.fun create
_cache = {}

def _cached(key, ttl):
    v = _cache.get(key)
    return v[1] if v and time.time() - v[0] < ttl else None

# ---------------- Preflight: what will this transaction do? ----------------
class PreflightIn(BaseModel):
    transaction: str          # base64 serialized tx (signed or unsigned)
    owner: str | None = None  # wallet to report balance changes for (default: fee payer)

def _ix_warnings(msg, keys):
    warn = []
    for ix in msg.instructions:
        prog = str(keys[ix.program_id_index]); d = bytes(ix.data)
        accs = [str(keys[i]) for i in ix.accounts if i < len(keys)]
        if prog in (TOKEN, TOKEN22) and d:
            if d[0] in (4, 13): warn.append({"severity": "high", "code": "TOKEN_APPROVE", "message": f"Grants a delegate ({accs[1] if len(accs) > 1 else '?'}) spending rights over a token account"})
            elif d[0] == 6: warn.append({"severity": "high", "code": "SET_AUTHORITY", "message": "Changes the owner/authority of a token account or mint"})
            elif d[0] == 9: warn.append({"severity": "low", "code": "CLOSE_ACCOUNT", "message": "Closes a token account (rent goes to the destination)"})
        if prog == SYSTEM and d[:4] == (1).to_bytes(4, "little"):
            warn.append({"severity": "high", "code": "SYSTEM_ASSIGN", "message": "Assigns an account to another program (common wallet-drainer pattern)"})
    unknown = sorted({str(keys[ix.program_id_index]) for ix in msg.instructions} - set(LABELS))
    if unknown: warn.append({"severity": "medium", "code": "UNKNOWN_PROGRAMS", "message": f"Invokes unlabeled program(s): {', '.join(unknown)}"})
    return warn

@app.post("/v1/tx/preflight")
async def tx_preflight(body: PreflightIn):
    try:
        tx = VersionedTransaction.from_bytes(base64.b64decode(body.transaction))
    except Exception as e:
        raise HTTPException(400, f"could not decode transaction: {e}")
    msg = tx.message; keys = list(msg.account_keys)
    owner = body.owner or str(keys[0])
    if not B58.match(owner): raise HTTPException(400, "invalid owner")
    writable = [str(k) for i, k in enumerate(keys) if msg.is_maybe_writable(i)][:40]
    async with httpx.AsyncClient(timeout=30) as c:
        try:
            pre = (await rpc(c, "getMultipleAccounts", [writable, {"encoding": "jsonParsed"}]))["value"]
            sim = (await rpc(c, "simulateTransaction", [body.transaction, {"encoding": "base64", "sigVerify": False,
                   "replaceRecentBlockhash": True, "accounts": {"encoding": "jsonParsed", "addresses": writable}}]))["value"]
        except RpcError as e:
            raise HTTPException(502, f"upstream RPC error: {e}")
    post = sim.get("accounts") or [None] * len(writable)
    sol, tokens = 0, {}
    for addr, a, b in zip(writable, pre, post):
        la, lb = (a or {}).get("lamports", 0), (b or {}).get("lamports", 0)
        if addr == owner: sol = (lb - la) / 1e9
        for side, acc in (("pre", a), ("post", b)):
            info = ((acc or {}).get("data") or {})
            info = info.get("parsed", {}).get("info") if isinstance(info, dict) else None
            if info and info.get("owner") == owner and "tokenAmount" in info:
                t = tokens.setdefault(info["mint"], {"pre": 0.0, "post": 0.0})
                t[side] = float(info["tokenAmount"].get("uiAmountString") or 0)
        # a token account closed in simulation shows post=None -> post stays 0
    changes = [{"mint": m, "change": round(v["post"] - v["pre"], 12)} for m, v in tokens.items() if v["post"] != v["pre"]]
    from app.main import _explain
    warns = _ix_warnings(msg, keys)
    if sol < -1: warns.append({"severity": "medium", "code": "LARGE_SOL_OUTFLOW", "message": f"Owner loses {-sol:.4f} SOL"})
    out = _explain(sim.get("err"), sim.get("logs"), sim.get("unitsConsumed"))
    return {"owner": owner, "would_succeed": sim.get("err") is None, "sol_change": sol, "token_changes": changes,
            "warnings": warns, "programs": [{"id": p, "label": LABELS.get(p)} for p in dict.fromkeys(str(keys[ix.program_id_index]) for ix in msg.instructions)],
            "uses_address_lookup_tables": bool(getattr(msg, "address_table_lookups", None)),
            "note": "Balance changes cover static writable accounts only; accounts loaded via lookup tables are not diffed.",
            **{k: out[k] for k in ("summary", "suggestion", "failing_instruction", "failing_program", "compute_units_consumed", "logs")}}

# ---------------- Wallet activity: decoded recent transactions ----------------
def _owner_deltas(tx, owner):
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
    m = tx["meta"]; sol = 0.0
    if owner in keys:
        i = keys.index(owner); sol = (m["postBalances"][i] - m["preBalances"][i]) / 1e9
    d = {}
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            if b.get("owner") == owner:
                d[b["mint"]] = d.get(b["mint"], 0) + sign * float(b["uiTokenAmount"].get("uiAmountString") or 0)
    progs = []
    for ix in tx["transaction"]["message"]["instructions"]:
        p = ix.get("programId"); 
        if p and p not in progs: progs.append(p)
    return sol, {k: round(v, 12) for k, v in d.items() if abs(v) > 0}, progs

def _classify(sol, toks, progs):
    labels = [LABELS.get(p) for p in progs]
    ins = [m for m, v in toks.items() if v > 0]; outs = [m for m, v in toks.items() if v < 0]
    if any(l in ("Jupiter v6", "Pump.fun", "PumpSwap", "Raydium AMM v4", "Raydium CPMM", "Raydium CLMM", "Orca Whirlpool", "Meteora DLMM", "Meteora DAMM v2") for l in labels):
        return "swap"
    if ins and not outs: return "receive"
    if outs and not ins: return "send"
    if not toks and abs(sol) > 0.00001: return "sol_transfer"
    return "other"

@app.get("/v1/activity/{address}")
async def activity(address: str, limit: int = 10):
    if not B58.match(address): raise HTTPException(400, "invalid base58 address")
    limit = max(1, min(limit, 25))
    hit = _cached(("act", address, limit), 30)
    if hit: return {**hit, "cached": True}
    async with httpx.AsyncClient(timeout=40) as c:
        try:
            sigs = await rpc(c, "getSignaturesForAddress", [address, {"limit": limit}])
            txs = await asyncio.gather(*[rpc(c, "getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}]) for s in sigs])
        except RpcError as e:
            raise HTTPException(502, f"upstream RPC error: {e}")
    rows, net_sol, net = [], 0.0, {}
    for s, tx in zip(sigs, txs):
        if not tx: continue
        sol, toks, progs = _owner_deltas(tx, address)
        net_sol += sol
        for k, v in toks.items(): net[k] = round(net.get(k, 0) + v, 12)
        rows.append({"signature": s["signature"], "block_time": s.get("blockTime"), "failed": s.get("err") is not None,
                     "type": _classify(sol, toks, progs), "sol_change": sol, "token_changes": toks,
                     "programs": [LABELS.get(p, p) for p in progs], "fee_sol": tx["meta"]["fee"] / 1e9})
    res = {"address": address, "transactions": rows, "window_net_sol": round(net_sol, 9), "window_net_tokens": net,
           "counts": {t: sum(r["type"] == t for r in rows) for t in ("swap", "send", "receive", "sol_transfer", "other")},
           "note": "Raw token amounts from on-chain balances; no USD prices or third-party labels."}
    _cache[("act", address, limit)] = (time.time(), res)
    return {**res, "cached": False}

# ---------------- New pump.fun launches feed ----------------
@app.get("/v1/launches")
async def launches(limit: int = 10, with_safety: bool = False):
    limit = max(1, min(limit, 20))
    key = ("launch", limit, with_safety)
    hit = _cached(key, 20)
    if hit: return {**hit, "cached": True}
    async with httpx.AsyncClient(timeout=40) as c:
        try:
            sigs = [s for s in await rpc(c, "getSignaturesForAddress", [PUMP_MINT_AUTHORITY, {"limit": limit * 2}]) if not s.get("err")][:limit]
            txs = await asyncio.gather(*[rpc(c, "getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}]) for s in sigs])
        except RpcError as e:
            raise HTTPException(502, f"upstream RPC error: {e}")
    out = []
    for s, tx in zip(sigs, txs):
        if not tx: continue
        m = tx["meta"]; keys = [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
        mint = next((b["mint"] for b in (m.get("postTokenBalances") or []) if b["mint"].endswith("pump")), None) \
            or next((b["mint"] for b in (m.get("postTokenBalances") or [])), None)
        if not mint: continue
        creator = keys[0]
        dev_buy = sum(float(b["uiTokenAmount"].get("uiAmountString") or 0) for b in (m.get("postTokenBalances") or [])
                      if b["mint"] == mint and b.get("owner") == creator)
        name = next((re.search(r"name: ([^,]+)", l).group(1) for l in (m.get("logMessages") or []) if "name:" in l and re.search(r"name: ([^,]+)", l)), None)
        out.append({"mint": mint, "creator": creator, "signature": s["signature"], "block_time": s.get("blockTime"),
                    "creator_initial_tokens": dev_buy, "creator_sol_spent": round(-(m["postBalances"][0] - m["preBalances"][0]) / 1e9, 6)})
    if with_safety:
        res = await asyncio.gather(*[analyze(o["mint"]) for o in out], return_exceptions=True)
        for o, r in zip(out, res):
            o["safety"] = None if isinstance(r, Exception) else {"risk_score": r["risk_score"], "risk_level": r["risk_level"],
                                                                 "flags": [f["code"] for f in r["flags"]]}
    data = {"source": "pump.fun creates (on-chain, via mint-authority account)", "launches": out}
    _cache[key] = (time.time(), data)
    return {**data, "cached": False}
