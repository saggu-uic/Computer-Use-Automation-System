# rep_20260915_155319_ecb9

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** failure · `APP_ERROR`
- **duration:** 1.1 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:53:19  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
20:53:20  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:53:20  replay         replay click link "MEMBER INQ" (nav frame)
20:53:20  replay         click_member_inq_link: screen member_inquiry → continue
20:53:20  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:53:20  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:53:20  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:53:20  replay         replay click button "GO" (main frame)
20:53:20  system         result: failure APP_ERROR
```
