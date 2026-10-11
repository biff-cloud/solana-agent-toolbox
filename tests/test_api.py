import os; os.environ.setdefault("X402_ENABLED", "0")
import os, pytest
from fastapi.testclient import TestClient
from app import main
client = TestClient(main.app)
def test_health(): assert client.get("/health").json() == {"status": "ok"}
def test_bad_mint(): assert client.get("/v1/token/not-a-mint!").status_code == 400
def test_score(): assert main.score([{"severity": "high"}]*2) == (60, "high")
@pytest.mark.skipif(not os.getenv("LIVE"), reason="live RPC")
def test_usdc_live():
    r = client.get("/v1/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").json()
    assert r["authorities"]["mint_authority"] and r["authorities"]["freeze_authority"] and r["token"]["decimals"] == 6
@pytest.mark.skipif(not os.getenv("LIVE"), reason="live RPC")
def test_not_mint_live():
    assert client.get("/v1/token/So11111111111111111111111111111111111111111x"[:44]).status_code in (404, 400)

def test_concentration_mocked(monkeypatch):
    M = "So1aMintAddr1111111111111111111111111111111"
    async def fake(c, method, params):
        if method == "getAccountInfo":
            return {"value": {"owner": main.TOKEN, "data": {"parsed": {"type": "mint", "info": {
                "decimals": 0, "supply": "1000", "mintAuthority": None, "freezeAuthority": None}}}}}
        if method == "getTokenLargestAccounts":
            return {"value": [{"address": "A1", "amount": "400"}, {"address": "A2", "amount": "300"}]}
        if params[0] == ["A1", "A2"]:
            return {"value": [{"data": {"parsed": {"info": {"owner": "POOLPDA"}}}}, {"data": {"parsed": {"info": {"owner": "WHALE"}}}}]}
        return {"value": [{"owner": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"} if o == "POOLPDA" else {"owner": "11111111111111111111111111111111"} for o in params[0]]}
    monkeypatch.setattr(main, "rpc", fake); main._cache.clear()
    r = client.get(f"/v1/token/{M}").json()
    assert r["concentration"]["top1_pct"] == 30.0 and r["liquidity"]["pools"][0]["venue"] == "Raydium AMM v4"
    assert "TOP_HOLDER_GT_20PCT" in [f["code"] for f in r["flags"]]
