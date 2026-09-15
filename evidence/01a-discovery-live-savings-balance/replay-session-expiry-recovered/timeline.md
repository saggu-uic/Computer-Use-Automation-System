# rep_20260915_122004_3b12

- **kind:** replay
- **subject:** fakebank.member.get_regular_savings_balance@1.0.0
- **status:** success
- **duration:** 2.2 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
17:20:04  system         replay fakebank.member.get_regular_savings_balance@1.0.0 (unattended) · no model in the loop
17:20:04  session        policy allow: type OPERATOR ID field on SIGN ON (safe, rule R6)
17:20:04  session        session type OPERATOR ID field on SIGN ON value {{secret:fakebank/operator_id}}
17:20:04  session        policy allow: type PASSWORD field on SIGN ON (safe, rule R6)
17:20:04  session        session type PASSWORD field on SIGN ON value {{secret:fakebank/operator_password}}
17:20:04  session        policy allow: click SIGN ON button (safe, rule R3)
17:20:04  session        session click SIGN ON button
17:20:04  session        sign_on: screen main_menu → continue
17:20:04  session        signed on (credentials typed by code from the profile; never shown to any model)
17:20:04  replay         policy allow: click link "MEMBER INQUIRY" (main frame) (safe, rule R6)
17:20:05  replay         replay click link "MEMBER INQUIRY" (main frame)
17:20:05  replay         click_member_inquiry_link: screen member_inquiry → continue
17:20:05  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:20:05  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:20:05  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:20:05  replay         replay click button "GO" (main frame)
17:20:05  recovery       recovery: SESSION_RENEWED_RESTARTED (re-login, restart from the start screen)
17:20:05  session        policy allow: type OPERATOR ID field on SIGN ON (safe, rule R6)
17:20:05  session        session type OPERATOR ID field on SIGN ON value {{secret:fakebank/operator_id}}
17:20:05  session        policy allow: type PASSWORD field on SIGN ON (safe, rule R6)
17:20:05  session        session type PASSWORD field on SIGN ON value {{secret:fakebank/operator_password}}
17:20:05  session        policy allow: click SIGN ON button (safe, rule R3)
17:20:05  session        session click SIGN ON button
17:20:05  session        sign_on: screen main_menu → continue
17:20:05  session        signed on (credentials typed by code from the profile; never shown to any model)
17:20:05  replay         policy allow: click link "MEMBER INQUIRY" (main frame) (safe, rule R6)
17:20:05  replay         replay click link "MEMBER INQUIRY" (main frame)
17:20:05  replay         click_member_inquiry_link: screen member_inquiry → continue
17:20:05  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:20:06  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:20:06  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:20:06  replay         replay click button "GO" (main frame)
17:20:06  replay         click_go_button: screen member_detail → continue
17:20:06  replay         extracted regular_savings_balance (money): «financial»
17:20:06  system         result: success
```
