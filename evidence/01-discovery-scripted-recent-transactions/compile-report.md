# Compile report

- **capability:** `fakebank.member.recent_transactions` 1.1.0 (draft)
- **from run:** `disc_20260915_125449_1d24` · goal: Show recent transactions for member {{value_1}}
- **steps:** 5 kept, 0 dropped
- **inputs:** `member_number` (string ^[0-9]+$, pii) named from textbox labelled "MEMBER #"
- **outputs:** `recent_transactions` (table, financial)
- **outcomes wired from the profile catalog:** MEMBER_NOT_FOUND, INVALID_MEMBER_NUMBER, ACCOUNT_RESTRICTED, NO_TRANSACTIONS
- **outcomes proposed by the compiler:** none
- **targets:** 5 · ≥1 verified durable locator: 5 · ≥2: 2 · fragile-only: 0

## Review flags
- none

## Errors
- none
