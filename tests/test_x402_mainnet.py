"""Production config (Solana mainnet): payTo guard and the 402 that buyers see. Offline: local facilitator keypair, no RPC call for unpaid 402s."""
import base64, json, os, subprocess, sys

MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
J41T = "J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d"

def run(tmp_path, **extra):
    from solders.keypair import Keypair
    kp = tmp_path / "fp.json"; kp.write_text(json.dumps(list(bytes(Keypair()))))
    code = r'''
import base64
from fastapi.testclient import TestClient
from app.main import app
r = TestClient(app).get("/v1/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
assert r.status_code == 402, r.status_code
print(base64.b64decode(r.headers["payment-required"]).decode())
'''
    env = {k: v for k, v in os.environ.items() if k != "PAY_TO_ADDRESS"}
    env.update({"X402_ENABLED": "1", "X402_NETWORK": MAINNET, "FACILITATOR_KEYPAIR": str(kp), "PYTHONPATH": "."}, **extra)
    return subprocess.run([sys.executable, "-W", "ignore", "-c", code], env=env, capture_output=True, text=True)

def test_mainnet_refuses_default_payto(tmp_path):
    out = run(tmp_path)
    assert out.returncode != 0 and "explicit PAY_TO_ADDRESS" in out.stderr

def test_mainnet_402_pays_usdc_to_payto(tmp_path):
    out = run(tmp_path, PAY_TO_ADDRESS=J41T)
    assert out.returncode == 0, out.stderr[-2000:]
    a = json.loads(out.stdout.strip().splitlines()[-1])["accepts"][0]
    assert a["network"] == MAINNET and a["payTo"] == J41T and a["amount"] == "5000"
    assert a["asset"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
