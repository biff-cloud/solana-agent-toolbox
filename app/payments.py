"""x402 pay-per-call (Solana, USDC) for the paid /v1/* routes.

Env:
  X402_ENABLED       "1" (default) / "0"
  PAY_TO_ADDRESS     wallet that receives USDC (receive-only; no key needed)
  X402_NETWORK       CAIP-2 id; default Solana devnet solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1
                     (mainnet: solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp)
  PAY_ASSET          SPL mint paid in; default = USDC of X402_NETWORK
  PRICE_ATOMIC       price in token base units; default 5000 (= $0.005 USDC)
  FACILITATOR_URL    use a remote facilitator (e.g. https://facilitator.payai.network); OR
  FACILITATOR_KEYPAIR path to a JSON keypair to self-facilitate (pays tx fees in SOL, holds no revenue)
  PAYMENT_RPC_URL    RPC for verifying/settling payments (default: network default)
"""
import json, os
from solders.keypair import Keypair
from x402 import x402ResourceServer, x402Facilitator
from x402.http import HTTPFacilitatorClient
from x402.http.types import RouteConfig, PaymentOption
from x402.http.middleware.fastapi import payment_middleware
from x402.mechanisms.svm.exact import ExactSvmServerScheme, ExactSvmFacilitatorScheme
from x402.mechanisms.svm.signers import FacilitatorKeypairSigner
from x402.mechanisms.svm import SOLANA_DEVNET_CAIP2, SOLANA_MAINNET_CAIP2, USDC_DEVNET_ADDRESS, USDC_MAINNET_ADDRESS
from x402.extensions.bazaar import declare_discovery_extension
from x402.extensions.bazaar.resource_service import OutputConfig

DEFAULT_DEVNET_PAY_TO = "9ZPaDj2Qcg4gXXEhArSNmtdHHSrXPWKmsDecnt94D44V"  # throwaway, generated locally (devnet/payto.json)

def config():
    net = os.getenv("X402_NETWORK", SOLANA_DEVNET_CAIP2)
    return {
        "enabled": os.getenv("X402_ENABLED", "1") == "1",
        "network": net,
        "pay_to": os.getenv("PAY_TO_ADDRESS", DEFAULT_DEVNET_PAY_TO),
        "asset": os.getenv("PAY_ASSET", USDC_MAINNET_ADDRESS if net == SOLANA_MAINNET_CAIP2 else USDC_DEVNET_ADDRESS),
        "price": os.getenv("PRICE_ATOMIC", "5000"),
        "facilitator_url": os.getenv("FACILITATOR_URL"),
        "facilitator_keypair": os.getenv("FACILITATOR_KEYPAIR"),
        "rpc": os.getenv("PAYMENT_RPC_URL"),
    }

ROUTES = {
    "GET /v1/token/:mint": ("Solana token safety: risk score, mint/freeze authority, Token-2022 traps, holder concentration, DEX pool share",
                        ["solana", "token-safety", "rug-check"], None,
                        {"risk_score": 4, "risk_level": "low", "flags": [], "authorities": {"mint_authority": None, "freeze_authority": None}}),
    "GET /v1/tx/:signature": ("Explain why a Solana transaction failed (by signature): decoded error, failing program, fix suggestion",
                     ["solana", "transaction", "debug"], None,
                     {"success": False, "summary": "Instruction #2 (SPL Token) failed: Insufficient token funds", "suggestion": "Lower the amount..."}),
    "POST /v1/tx/simulate": ("Simulate a base64 Solana transaction and explain the failure before sending",
                             ["solana", "simulate", "debug"], {"transaction": "<base64 tx>"},
                             {"success": False, "summary": "...", "logs": []}),
    "GET /v1/wallet/:address": ("Solana wallet snapshot: SOL, token holdings, frozen/closable accounts, recent activity",
                         ["solana", "wallet", "portfolio"], None,
                         {"sol": 1.2, "tokens": [], "empty_accounts_closable": 3}),
}

def build_routes(cfg):
    out = {}
    for key, (desc, tags, body_example, out_example) in ROUTES.items():
        ext = declare_discovery_extension(
            input=body_example, body_type="json" if key.startswith("POST") else None,
            input_schema={"properties": {"transaction": {"type": "string"}}, "required": ["transaction"]} if body_example else None,
            output=OutputConfig(example=out_example))
        out[key] = RouteConfig(
            accepts=PaymentOption(scheme="exact", pay_to=cfg["pay_to"], network=cfg["network"],
                                  price={"amount": cfg["price"], "asset": cfg["asset"], "extra": {}}, max_timeout_seconds=120),
            description=desc, mime_type="application/json", service_name="Solana Agent Toolbox", tags=tags, extensions=ext)
    return out

def build_server(cfg):
    if cfg["facilitator_url"]:
        fac = HTTPFacilitatorClient({"url": cfg["facilitator_url"]})
    else:
        path = cfg["facilitator_keypair"]
        if not path: raise RuntimeError("set FACILITATOR_URL or FACILITATOR_KEYPAIR")
        kp = Keypair.from_bytes(bytes(json.load(open(path))))
        fac = x402Facilitator()
        fac.register([cfg["network"]], ExactSvmFacilitatorScheme(FacilitatorKeypairSigner(kp, rpc_url=cfg["rpc"])))
    server = x402ResourceServer(fac)
    server.register(cfg["network"], ExactSvmServerScheme(rpc_url=cfg["rpc"]))
    return server

def install(app):
    cfg = config()
    if not cfg["enabled"]: return
    if cfg["network"] == SOLANA_MAINNET_CAIP2 and not os.getenv("PAY_TO_ADDRESS"):
        raise RuntimeError("mainnet requires an explicit PAY_TO_ADDRESS (refusing to use the throwaway devnet default)")
    mw = payment_middleware(build_routes(cfg), build_server(cfg))
    @app.middleware("http")
    async def x402_mw(request, call_next):
        return await mw(request, call_next)

    @app.get("/.well-known/x402")
    async def discovery():
        return {"x402Version": 2, "service": "Solana Agent Toolbox", "network": cfg["network"], "payTo": cfg["pay_to"],
                "asset": cfg["asset"], "priceAtomic": cfg["price"],
                "resources": [{"route": k, "description": v[0], "tags": v[1]} for k, v in ROUTES.items()]}
