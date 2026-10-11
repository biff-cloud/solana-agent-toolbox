"""Decode Solana transaction errors into plain-English explanations."""
import re

SYSTEM = {0: "Account already in use", 1: "Insufficient SOL (lamports) for the transfer or account creation",
          2: "Invalid program id", 3: "Invalid account data length", 4: "Max seed length exceeded",
          5: "Address does not match derived seed", 6: "Nonce no recent blockhashes", 7: "Nonce blockhash not expired"}
SPL_TOKEN = {0: "Lamport balance below rent-exempt threshold", 1: "Insufficient token funds", 2: "Invalid mint",
             3: "Account not associated with this mint", 4: "Owner does not match", 5: "Fixed supply (no mint authority)",
             6: "Account already in use", 7: "Invalid number of provided signers", 8: "Invalid number of required signers",
             9: "State is uninitialized", 10: "Instruction does not support native tokens", 11: "Non-native account has non-zero balance",
             12: "Invalid instruction", 13: "State is invalid for requested operation", 14: "Operation overflowed",
             15: "Account does not support specified authority type", 16: "This token mint cannot freeze accounts",
             17: "Account is frozen", 18: "Mint decimals mismatch", 19: "Instruction does not support non-native tokens"}
ANCHOR = {100: "Instruction discriminator not provided", 101: "Instruction fallback not found", 102: "Instruction did not deserialize",
          2000: "Constraint mut violated (account must be writable)", 2001: "has_one constraint violated", 2002: "Signer constraint violated",
          2003: "Raw constraint violated", 2006: "Seeds constraint violated (wrong PDA)", 2012: "Address constraint violated",
          2014: "Token mint constraint violated", 2015: "Token owner constraint violated",
          3001: "Account discriminator mismatch", 3002: "Account discriminator not found", 3003: "Account did not deserialize",
          3007: "Account owned by a different program", 3010: "Account not signer", 3011: "Account not system-owned",
          3012: "Account not initialized (AccountNotInitialized)", 3014: "Account not a program", 3016: "Account not associated token account",
          6000: "Program-specific custom error #0 (see the program's IDL)"}
JUP_SLIPPAGE = {6001: "Slippage tolerance exceeded (Jupiter)", 6017: "Exact-out amount not matched (Jupiter)"}
PROGRAMS = {"11111111111111111111111111111111": ("System Program", SYSTEM),
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": ("SPL Token", SPL_TOKEN),
            "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": ("Token-2022", SPL_TOKEN),
            "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": ("Jupiter v6", JUP_SLIPPAGE)}
TOP = {"BlockhashNotFound": ("Blockhash expired or not found", "Fetch a fresh blockhash and re-sign; send sooner or use a durable nonce."),
       "InsufficientFundsForFee": ("Fee payer lacks SOL for the fee", "Top up the fee payer with SOL."),
       "InsufficientFundsForRent": ("An account would be left below rent-exempt minimum", "Send more SOL or close accounts properly."),
       "AccountNotFound": ("Fee payer / account does not exist on chain", "Fund the address first."),
       "AlreadyProcessed": ("Transaction already processed", "Do not resend; check the original signature."),
       "ProgramAccountNotFound": ("Invoked program does not exist on this cluster", "Check the program id and cluster (mainnet vs devnet)."),
       "SignatureFailure": ("Signature verification failed", "Sign with every required signer."),
       "WouldExceedMaxBlockCostLimit": ("Block full", "Retry with a priority fee."),
       "WouldExceedAccountDataBlockLimit": ("Block account-data limit reached", "Retry later.")}
GENERIC = {"InsufficientFunds": "Insufficient funds", "InvalidAccountData": "Invalid account data for this instruction",
           "AccountAlreadyInitialized": "Account already initialized", "UninitializedAccount": "Account is not initialized",
           "MissingRequiredSignature": "A required signature is missing", "IncorrectProgramId": "Account owned by the wrong program",
           "AccountDataTooSmall": "Account data too small", "ComputationalBudgetExceeded": "Ran out of compute units",
           "ProgramFailedToComplete": "Program failed to complete (often compute budget exhausted)",
           "PrivilegeEscalation": "Cross-program call escalated signer/writable privilege", "InvalidSeeds": "Wrong PDA seeds",
           "ExternalAccountLamportSpend": "Program debited an account it does not own", "ReadonlyLamportChange": "Lamports changed on a read-only account"}
FIX = {"Insufficient token funds": "Lower the amount or top up the source token account.",
       "Insufficient SOL (lamports) for the transfer or account creation": "Add SOL to the paying account.",
       "Slippage tolerance exceeded (Jupiter)": "Re-quote and retry with higher slippage or smaller size.",
       "Ran out of compute units": "Add a ComputeBudget setComputeUnitLimit instruction with a higher limit.",
       "Account not initialized (AccountNotInitialized)": "Create/initialize the account (e.g. the associated token account) first.",
       "Account is frozen": "The token issuer froze this account; only the freeze authority can thaw it.",
       "Owner does not match": "Sign with the token account's owner or use the right source account."}

def _failing_program(logs, idx):
    """Find the program whose top-level invocation index matches the failing instruction."""
    top = [m.group(1) for l in logs for m in [re.match(r"Program (\w+) invoke \[1\]", l)] if m]
    failed = [m.group(1) for l in logs for m in [re.match(r"Program (\w+) failed", l)] if m]
    if failed: return failed[-1]
    return top[idx] if idx is not None and idx < len(top) else None

def explain_error(err, logs):
    if err is None:
        return {"summary": "Transaction succeeded (no error)", "suggestion": None, "failing_instruction": None, "failing_program": None}
    if isinstance(err, str):
        s, fix = TOP.get(err, (f"Transaction-level error: {err}", None))
        return {"summary": s, "suggestion": fix, "failing_instruction": None, "failing_program": None}
    if isinstance(err, dict) and "InstructionError" in err:
        idx, inner = err["InstructionError"]
        prog = _failing_program(logs, idx)
        name, table = PROGRAMS.get(prog, (prog, {}))
        if isinstance(inner, dict) and "Custom" in inner:
            code = inner["Custom"]
            msg = table.get(code) or ANCHOR.get(code) or f"Custom program error {code} (0x{code:x}); look it up in the program's IDL/error enum"
        else:
            key = inner if isinstance(inner, str) else next(iter(inner))
            msg = GENERIC.get(key, key)
        anchor_log = next((l for l in logs if "AnchorError" in l or "Error Message:" in l), None)
        return {"summary": f"Instruction #{idx} ({name or 'unknown program'}) failed: {msg}", "suggestion": FIX.get(msg),
                "failing_instruction": idx, "failing_program": prog, "program_log_hint": anchor_log}
    if isinstance(err, dict) and "InsufficientFundsForRent" in err:
        return {"summary": "An account would be left below the rent-exempt minimum", "suggestion": TOP["InsufficientFundsForRent"][1],
                "failing_instruction": None, "failing_program": None}
    return {"summary": f"Unrecognised error: {err}", "suggestion": None, "failing_instruction": None, "failing_program": None}
