"""Read-only USDC inflow tracker for the payTo wallet (no keys)."""
import os, time, httpx
from solders.pubkey import Pubkey
from spl.token.instructions import get_associated_token_address

def ata_of(owner: str, mint: str) -> str:
    return str(get_associated_token_address(Pubkey.from_string(owner), Pubkey.from_string(mint)))

def _rpc(url, method, params):
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
    r.raise_for_status(); j = r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j["result"]

def inflows(owner: str, mint: str, rpc_url: str, limit: int = 25, price_atomic: int | None = None, since: int = 0):
    """If price_atomic is set, only count inflows of exactly that size (x402 call payments) after `since`."""
    ata = ata_of(owner, mint)
    sigs = _rpc(rpc_url, "getSignaturesForAddress", [ata, {"limit": limit}])
    rows, total = [], 0
    for s in sigs:
        if s.get("err"): continue
        tx = _rpc(rpc_url, "getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        if not tx: continue
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in tx["transaction"]["message"]["accountKeys"]]
        def amt(bals):
            for b in bals or []:
                if keys[b["accountIndex"]] == ata: return int(b["uiTokenAmount"]["amount"])
            return 0
        delta = amt(tx["meta"].get("postTokenBalances")) - amt(tx["meta"].get("preTokenBalances"))
        if delta > 0 and (price_atomic is None or delta == price_atomic) and (s.get("blockTime") or 0) >= since:
            total += delta
            rows.append({"signature": s["signature"], "block_time": s.get("blockTime"), "usdc": delta / 1e6})
    return {"payTo": owner, "usdc_account": ata, "scanned_signatures": len(sigs), "filter_price_atomic": price_atomic,
            "since": since, "paid_calls_onchain": len(rows), "revenue_usdc": total / 1e6, "payments": rows, "checked_at": int(time.time())}

if __name__ == "__main__":
    import json, sys
    owner = sys.argv[1] if len(sys.argv) > 1 else os.environ["PAY_TO_ADDRESS"]
    print(json.dumps(inflows(owner, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                             os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"), int(os.getenv("LIMIT", "25")),
                             int(os.getenv("PRICE_ATOMIC", "5000")), int(os.getenv("REVENUE_SINCE", "0"))), indent=1))
