from app.swap import fee_plan, fee_account, USDC, WSOL

def test_fee_plan():
    assert fee_plan(WSOL, USDC) == (USDC, 50)
    assert fee_plan("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", WSOL) == (WSOL, 50)
    assert fee_plan("A", "B") == (None, 0)

def test_fee_accounts_are_owner_atas():
    assert fee_account(USDC) == "3MqYKJmcY3Wzmw2Laqr5n1a796eQszzUJFFSJgJgEMVV"
    assert fee_account(WSOL) == "Dxfqzeq2b3cYoBQUsBMH4fKrzAawR94JaTT91G6MeaJg"
