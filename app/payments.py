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
import json, os, re, time
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
    "POST /v1/tx/preflight": ("Solana transaction preflight before signing: simulates a base64 tx and returns the owner's SOL/token balance changes, "
                              "drainer warnings (token approve/delegate, set-authority, system assign), unknown programs and decoded errors. Wallet-safety check for agents.",
                              ["solana", "preflight", "simulate", "wallet-safety", "drainer-detection"], {"transaction": "<base64 tx>", "owner": "<optional wallet>"},
                              {"would_succeed": True, "sol_change": -0.01, "token_changes": [{"mint": "EPjF...", "change": 25.0}], "warnings": []}),
    "GET /v1/activity/:address": ("Solana wallet activity: last 1-25 transactions decoded into swap/send/receive with per-tx SOL and token balance changes, "
                                  "programs used (Jupiter, Raydium, Pump.fun...), fees and window net flows. Query: ?limit=10",
                                  ["solana", "wallet", "activity", "pnl", "transactions"], None,
                                  {"counts": {"swap": 3, "send": 1}, "transactions": [{"type": "swap", "sol_change": -0.5, "token_changes": {"<mint>": 1200.0}}]}),
    "GET /v1/swap/quote": ("Solana swap quote (Jupiter routing) + output-token rug/safety check in one call: out amount, min out, price impact, route, "
                           "risk score/flags. Query: inputMint, outputMint, amount (base units), slippageBps. Then POST /v1/swap/build (free) for an unsigned tx.",
                           ["solana", "swap", "quote", "jupiter", "token-safety", "trading"], None,
                           {"out_amount": "28691442689", "price_impact_pct": "0", "route": ["Raydium"], "output_token_safety": {"risk_score": 12, "risk_level": "low"}}),
    "GET /v1/launches": ("New Solana token launch feed: latest pump.fun token creations read on-chain (mint, creator, creator SOL spent, dev buy), "
                         "optional ?with_safety=true risk score per token. Query: ?limit=10 (max 20)",
                         ["solana", "new-tokens", "pump.fun", "launches", "sniping", "memecoin"], None,
                         {"launches": [{"mint": "...pump", "creator": "...", "creator_sol_spent": 0.35, "safety": {"risk_score": 10, "risk_level": "low"}}]}),
}

EXAMPLE_URLS = {
    "GET /v1/token/:mint": "/v1/token/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "GET /v1/tx/:signature": "/v1/tx/5674E4Gkoj3Z3nLjMYNr7GrBHpefbge39FhWTZnMod1outJwwuduuX9r6gzh2YvUbHmkcsrC2a2m2yRcfVmX6f7s",
    "POST /v1/tx/simulate": "/v1/tx/simulate",
    "GET /v1/wallet/:address": "/v1/wallet/J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d",
    "POST /v1/tx/preflight": "/v1/tx/preflight",
    "GET /v1/activity/:address": "/v1/activity/J41TaA2hn4YpASarwJK3oByxrKkpQDRVWaNyNCFV6o3d",
    "GET /v1/launches": "/v1/launches?limit=10",
    "GET /v1/swap/quote": "/v1/swap/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000000",
}

QUERY_EX = {"GET /v1/swap/quote": {"inputMint": "So11111111111111111111111111111111111111112", "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "amount": 100000000}}
QUERY_SCHEMA = {"GET /v1/swap/quote": {"properties": {"inputMint": {"type": "string"}, "outputMint": {"type": "string"}, "amount": {"type": "integer"},
                                                      "slippageBps": {"type": "integer"}}, "required": ["inputMint", "outputMint", "amount"]}}

def build_routes(cfg):
    out = {}
    for key, (desc, tags, body_example, out_example) in ROUTES.items():
        pname = key.split(":")[-1] if ":" in key else None
        if not pname and not body_example:  # query-only route
            ext = declare_discovery_extension(input=QUERY_EX.get(key, {"limit": 10}), input_schema=QUERY_SCHEMA.get(key, {"properties": {"limit": {"type": "integer"}, "with_safety": {"type": "boolean"}}}),
                                              output=OutputConfig(example=out_example))
        else:
          ext = declare_discovery_extension(
            input=body_example if body_example else {pname: EXAMPLE_URLS[key].rsplit("/", 1)[-1]},
            body_type="json" if key.startswith("POST") else None,
            path_params_schema={"type": "object", "properties": {pname: {"type": "string", "description": f"base58 {pname}"}}, "required": [pname]} if pname else None,
            input_schema={"properties": {"transaction": {"type": "string", "description": "base64 serialized transaction"}, "owner": {"type": "string"}}, "required": ["transaction"]} if body_example else {"properties": {pname: {"type": "string"}}, "required": [pname]},
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
    counters = {"since": int(time.time()), "paid_calls": 0, "unpaid_402": 0, "by_route": {}}
    @app.middleware("http")
    async def x402_mw(request, call_next):
        resp = await mw(request, call_next)
        if request.url.path.startswith("/v1/"):
            if resp.status_code == 402: counters["unpaid_402"] += 1
            elif resp.headers.get("payment-response"):
                counters["paid_calls"] += 1
                r = "/".join(request.url.path.split("/")[:3]); counters["by_route"][r] = counters["by_route"].get(r, 0) + 1
        return resp

    stats_cache = {}
    @app.get("/stats")
    async def stats():
        """Free: paid-call counters since last restart + on-chain USDC inflows to payTo (cached 10 min)."""
        from app.revenue import inflows
        import asyncio
        now = time.time()
        if not stats_cache or now - stats_cache["t"] > 600:
            try:
                data = await asyncio.to_thread(inflows, cfg["pay_to"], cfg["asset"], os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"), 50, int(cfg["price"]), int(os.getenv("REVENUE_SINCE", "1791752400")))
            except Exception as e:
                data = {"error": f"on-chain lookup failed: {e}"}
            stats_cache.update(t=now, data=data)
        return {"process": counters, "onchain": stats_cache["data"]}

    base = os.getenv("PUBLIC_BASE_URL", "https://solana-agent-toolbox.onrender.com").rstrip("/")

    @app.get("/.well-known/x402")
    async def discovery():
        # x402scan fan-out format ("version" + concrete resource URLs) plus richer route metadata
        return {"version": 1, "x402Version": 2, "service": "Solana Agent Toolbox",
                "resources": [base + u for u in EXAMPLE_URLS.values()],
                "network": cfg["network"], "payTo": cfg["pay_to"], "asset": cfg["asset"], "priceAtomic": cfg["price"],
                "routes": [{"route": k, "example": base + EXAMPLE_URLS[k], "description": v[0], "tags": v[1]} for k, v in ROUTES.items()],
                "instructions": "Each call costs $0.005 USDC on Solana mainnet via x402 v2 (exact). Unpaid requests return 402 with a PAYMENT-REQUIRED header."}

    # x402scan OpenAPI-first discovery: annotate paid operations
    usd = f"{int(cfg['price']) / 1e6:g}"
    orig_openapi = app.openapi
    def openapi_with_payment():
        if app.openapi_schema: return app.openapi_schema
        schema = orig_openapi()
        schema["info"]["title"] = "Solana Agent Toolbox (x402)"
        schema["info"]["description"] = ("Pay-per-call Solana tools for AI agents: token safety, tx preflight/simulation, error explainer, wallet snapshot & activity, new-launch feed, swap quote with safety check (free swap tx builder). "
                                         f"${usd} USDC per call on Solana mainnet via x402. Informational only, not financial advice.")
        schema["servers"] = [{"url": base}]
        for path, ops in schema["paths"].items():
            if not path.startswith("/v1/"): continue
            for meth, op in ops.items():
                key = f"{meth.upper()} " + re.sub(r"\{(\w+)\}", r":\1", path)
                if key not in ROUTES:   # free route (e.g. /v1/swap/build earns via swap fee)
                    op["security"] = []; continue
                op["x-payment-info"] = {"protocols": ["x402"], "price": {"mode": "fixed", "currency": "USD", "amount": usd},
                                        "network": cfg["network"], "asset": cfg["asset"], "payTo": cfg["pay_to"]}
                op.setdefault("responses", {})["402"] = {"description": "Payment Required (x402 v2; see PAYMENT-REQUIRED header)"}
        app.openapi_schema = schema
        return schema
    app.openapi = openapi_with_payment
