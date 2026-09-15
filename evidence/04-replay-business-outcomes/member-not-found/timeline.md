# rep_20260915_125511_c1cf

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** business_outcome · `MEMBER_NOT_FOUND`
- **duration:** 0.9 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
17:55:11  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
17:55:11  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
17:55:11  replay         replay click link "MEMBER INQ" (nav frame)
17:55:11  replay         click_member_inq_link: screen member_inquiry → continue
17:55:11  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:55:11  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:55:12  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:55:12  replay         replay click button "GO" (main frame)
17:55:12  replay         click_go_button: screen member_not_found → MEMBER_NOT_FOUND
17:55:12  system         result: business_outcome MEMBER_NOT_FOUND
```
