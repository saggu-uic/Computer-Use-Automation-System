# rep_20260915_155323_a61d

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** failure · `CHECKPOINT_TIMEOUT`
- **duration:** 13.1 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:53:23  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
20:53:23  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:53:23  replay         replay click link "MEMBER INQ" (nav frame)
20:53:23  replay         click_member_inq_link: screen member_inquiry → continue
20:53:23  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:53:23  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:53:24  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:53:24  replay         replay click button "GO" (main frame)
20:53:30  system         click_go_button: still loading after 6000 ms; waiting once more
20:53:36  system         result: failure CHECKPOINT_TIMEOUT
```
