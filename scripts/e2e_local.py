"""End-to-end x402 test against a Solana validator (local or devnet).

1. Builds a failing SPL transfer (insufficient funds) and lands it with skip_preflight, for the explainer.
2. Calls each paid endpoint with a plain client (expects 402) and with an x402 client that pays in the
   test USDC mint, then checks the payTo balance increased by the price per call.
Env: RPC (default http://127.0.0.1:8899), API (default http://127.0.0.1:8090), ASSET, PRICE_ATOMIC.
"""
import asyncio, base64, json, os, sys, time
import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.instruction import Instruction, AccountMeta
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.svm.exact import register_exact_svm_client
from x402.mechanisms.svm.signers import KeypairSigner

RPC = os.getenv("RPC", "http://127.0.0.1:8899"); API = os.getenv("API", "http://127.0.0.1:8090")
ASSET = Pubkey.from_string(os.environ["ASSET"]); PRICE = int(os.getenv("PRICE_ATOMIC", "5000"))
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROG = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
kp = lambda n: Keypair.from_bytes(bytes(json.load(open(f"devnet/{n}.json"))))
payer, payto, feepayer = kp("payer"), kp("payto"), kp("feepayer")
ata = lambda owner: Pubkey.find_program_address([bytes(owner), bytes(TOKEN), bytes(ASSET)], ATA_PROG)[0]
sol = Client(RPC)

def bal(owner):
    return int(sol.get_token_account_balance(ata(owner), commitment="confirmed").value.amount)

def failing_tx(sign=True):
    # SPL Token Transfer (ix 3) of 10^12 base units > balance
    data = bytes([3]) + (10**12).to_bytes(8, "little")
    ix = Instruction(TOKEN, data, [AccountMeta(ata(payer.pubkey()), False, True), AccountMeta(ata(payto.pubkey()), False, True),
                                   AccountMeta(payer.pubkey(), True, False)])
    bh = sol.get_latest_blockhash().value.blockhash
    msg = MessageV0.try_compile(feepayer.pubkey(), [ix], [], bh)
    return VersionedTransaction(msg, [feepayer, payer])

async def main():
    results = {}
    tx = failing_tx()
    sig = sol.send_raw_transaction(bytes(tx), opts=TxOpts(skip_preflight=True)).value
    time.sleep(3)
    tx_b64 = base64.b64encode(bytes(failing_tx())).decode()
    calls = [("GET", f"/v1/token/{ASSET}", None), ("GET", f"/v1/wallet/{payer.pubkey()}", None),
             ("GET", f"/v1/tx/{sig}", None), ("POST", "/v1/tx/simulate", {"transaction": tx_b64})]
    async with httpx.AsyncClient(base_url=API, timeout=60) as plain:
        for m, p, b in calls:
            r = await plain.request(m, p, json=b)
            results[f"unpaid {m} {p[:30]}"] = r.status_code
            assert r.status_code == 402, (p, r.status_code, r.text)
            assert "payment-required" in r.headers
    client = x402Client()
    client.set_spend_controls({"allowed_assets": [{"network": os.getenv("X402_NETWORK", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"), "asset": str(ASSET), "max_amount_per_payment": "10000"}]})
    register_exact_svm_client(client, KeypairSigner(payer), rpc_url=RPC)
    before = bal(payto.pubkey())
    async with x402HttpxClient(client, base_url=API, timeout=90) as paid:
        for m, p, b in calls:
            r = await paid.request(m, p, json=b)
            body = r.json()
            assert r.status_code == 200, (p, r.status_code, r.text[:500])
            key = f"paid {m} {p[:30]}"
            results[key] = {"status": 200, "settlement": json.loads(base64.b64decode(r.headers["payment-response"])) if r.headers.get("payment-response") else None,
                            "summary": body.get("summary") or body.get("risk_level") or body.get("token_accounts_nonzero")}
    time.sleep(5)
    after = bal(payto.pubkey())
    results["payTo_received_atomic"] = after - before
    results["expected_atomic"] = PRICE * len(calls)
    print(json.dumps(results, indent=1))
    assert after - before == PRICE * len(calls)
    print("E2E OK")

asyncio.run(main())
