from app.errors import explain_error
TOK = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
def test_spl_insufficient():
    logs = [f"Program {TOK} invoke [1]", "Program log: Error: insufficient funds", f"Program {TOK} failed: custom program error: 0x1"]
    r = explain_error({"InstructionError": [0, {"Custom": 1}]}, logs)
    assert "Insufficient token funds" in r["summary"] and r["suggestion"]
def test_blockhash(): assert "Blockhash" in explain_error("BlockhashNotFound", [])["summary"]
def test_anchor(): assert "AccountNotInitialized" in explain_error({"InstructionError": [1, {"Custom": 3012}]}, [])["summary"]
def test_generic(): assert "compute" in explain_error({"InstructionError": [0, "ComputationalBudgetExceeded"]}, [])["summary"].lower()
def test_ok(): assert explain_error(None, [])["failing_instruction"] is None
