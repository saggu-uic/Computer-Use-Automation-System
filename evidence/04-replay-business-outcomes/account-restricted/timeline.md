# rep_20260915_155255_5291

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** business_outcome · `ACCOUNT_RESTRICTED`
- **duration:** 0.8 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:52:55  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
20:52:55  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:52:55  replay         replay click link "MEMBER INQ" (nav frame)
20:52:55  replay         click_member_inq_link: screen member_inquiry → continue
20:52:55  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:52:55  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:52:56  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:52:56  replay         replay click button "GO" (main frame)
20:52:56  replay         click_go_button: screen access_restricted → ACCOUNT_RESTRICTED
20:52:56  system         result: business_outcome ACCOUNT_RESTRICTED
```
