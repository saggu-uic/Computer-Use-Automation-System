# rep_20260915_125518_f84a

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** success
- **duration:** 1.5 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
17:55:18  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
17:55:19  recovery       policy allow: click OK button on the SYSTEM NOTICE overlay (safe, rule default)
17:55:19  recovery       recovery click OK button on the SYSTEM NOTICE overlay
17:55:19  recovery       recovery: SYSTEM_NOTICE_DISMISSED (Maintenance notice overlay; dismissed and recorded as a recovery)
17:55:19  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
17:55:19  replay         replay click link "MEMBER INQ" (nav frame)
17:55:19  replay         click_member_inq_link: screen member_inquiry → continue
17:55:19  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:55:19  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:55:19  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:55:19  replay         replay click button "GO" (main frame)
17:55:20  replay         click_go_button: screen member_detail → continue
17:55:20  replay         extracted savings_balance (money): «financial»
17:55:20  system         result: success
```
