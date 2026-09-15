# rep_20260915_155256_ff9e

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** business_outcome · `NO_REGULAR_SAVINGS`
- **duration:** 1.0 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:52:56  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
20:52:56  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:52:57  replay         replay click link "MEMBER INQ" (nav frame)
20:52:57  replay         click_member_inq_link: screen member_inquiry → continue
20:52:57  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:52:57  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:52:57  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:52:57  replay         replay click button "GO" (main frame)
20:52:57  replay         click_go_button: screen member_detail → continue
20:52:57  system         result: business_outcome NO_REGULAR_SAVINGS
```
