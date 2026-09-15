# Compile report

- **capability:** `fakebank.member.get_member_since_date` 1.0.0 (draft)
- **from run:** `disc_20260915_153524_8a4c` · goal: When did member {{value_1}} become a member?
- **steps:** 4 kept, 0 dropped
- **inputs:** `member_number` (string ^[0-9]+$, pii) named from textbox labelled "MEMBER #"
- **outputs:** `member_since` (date, internal)
- **outcomes wired from the profile catalog:** MEMBER_NOT_FOUND, INVALID_MEMBER_NUMBER, ACCOUNT_RESTRICTED
- **outcomes proposed by the compiler:** none
- **targets:** 4 · ≥1 verified durable locator: 4 · ≥2: 2 · fragile-only: 0

## Review flags
- none

## Errors
- none
