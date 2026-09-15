# Compile report

- **capability:** `fakebank.member.get_savings_balance` 1.2.0 (draft)
- **from run:** `disc_20260915_155213_2926` · goal: What is the current regular savings balance for member {{value_1}}?
- **steps:** 4 kept, 0 dropped
- **inputs:** `member_number` (string ^[0-9]+$, pii) named from textbox labelled "MEMBER #"
- **outputs:** `savings_balance` (money, financial)
- **outcomes wired from the profile catalog:** MEMBER_NOT_FOUND, INVALID_MEMBER_NUMBER, ACCOUNT_RESTRICTED
- **outcomes proposed by the compiler:** NO_REGULAR_SAVINGS
- **targets:** 4 · ≥1 verified durable locator: 4 · ≥2: 2 · fragile-only: 0

## Review flags
- none

## Errors
- none
