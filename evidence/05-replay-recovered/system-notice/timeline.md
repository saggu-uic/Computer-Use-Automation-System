# rep_20260915_155259_65eb

- **kind:** replay
- **subject:** fakebank.member.get_savings_balance@1.1.0
- **status:** success
- **duration:** 1.4 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:52:59  system         replay fakebank.member.get_savings_balance@1.1.0 (unattended) · no model in the loop
20:53:00  recovery       policy allow: click OK button on the SYSTEM NOTICE overlay (safe, rule default)
20:53:00  recovery       recovery click OK button on the SYSTEM NOTICE overlay
20:53:00  recovery       recovery: SYSTEM_NOTICE_DISMISSED (Maintenance notice overlay; dismissed and recorded as a recovery)
20:53:00  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:53:00  replay         replay click link "MEMBER INQ" (nav frame)
20:53:00  replay         click_member_inq_link: screen member_inquiry → continue
20:53:00  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:53:00  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:53:00  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:53:00  replay         replay click button "GO" (main frame)
20:53:01  replay         click_go_button: screen member_detail → continue
20:53:01  replay         extracted savings_balance (money): «financial»
20:53:01  system         result: success
```
