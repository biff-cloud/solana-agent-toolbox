"""Unpaid requests to paid routes must get HTTP 402 with x402 v2 requirements; /health stays free."""
import base64, json, os, subprocess, sys

def test_402_requirements(tmp_path):
    from solders.keypair import Keypair
    kp = tmp_path / "fp.json"; kp.write_text(json.dumps(list(bytes(Keypair()))))
    code = r'''
import base64, json
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
assert c.get("/health").status_code == 200
for path in ["/v1/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "/v1/wallet/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"]:
    r = c.get(path); assert r.status_code == 402, r.status_code
    req = json.loads(base64.b64decode(r.headers["payment-required"]))
    a = req["accepts"][0]
    print(json.dumps(a))
d = c.get("/.well-known/x402").json(); assert len(d["resources"]) == 8
assert c.get("/v1/swap/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000").status_code == 402
assert c.post("/v1/swap/build", json={"user": "bad!"}).status_code == 400  # free route, no 402
assert c.app.openapi()["paths"]["/v1/swap/build"]["post"]["security"] == []
for m_, p_ in (("post", "/v1/tx/preflight"), ("get", "/v1/activity/J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d"), ("get", "/v1/launches?limit=3")):
    r = getattr(c, m_)(p_, **({"json": {"transaction": "AA=="}} if m_ == "post" else {})); assert r.status_code == 402, (p_, r.status_code)
'''
    env = {**os.environ, "X402_ENABLED": "1", "FACILITATOR_KEYPAIR": str(kp), "PYTHONPATH": "."}
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    a = json.loads(out.stdout.strip().splitlines()[-1])
    assert a["network"] == "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1" and a["asset"] == "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
    assert a["amount"] == "5000" and a["payTo"] == "9ZPaDj2Qcg4gXXEhArSNmtdHHSrXPWKmsDecnt94D44V"

