# rep_20260915_125557_1473

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** failure · `HUMAN_REQUIRED`
- **duration:** 1.5 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
17:55:57  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
17:55:58  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
17:55:58  replay         replay click link "MEMBER INQ" (nav frame)
17:55:58  replay         click_member_inq_link: screen member_inquiry → continue
17:55:58  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:55:58  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:55:58  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:55:58  replay         replay click button "GO" (main frame)
17:55:59  system         intervention int_81ec25 raised for routing (unattended): SUPERVISOR_OVERRIDE_REQUIRED
17:55:59  system         result: failure HUMAN_REQUIRED
```
