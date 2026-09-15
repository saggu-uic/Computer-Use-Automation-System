# rep_20260915_153558_b70d

- **kind:** replay
- **subject:** fakebank.member.get_regular_savings_balance@1.0.0
- **status:** business_outcome · `MEMBER_NOT_FOUND`
- **duration:** 1.4 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:35:58  system         replay fakebank.member.get_regular_savings_balance@1.0.0 (unattended) · no model in the loop
20:35:58  session        policy allow: type OPERATOR ID field on SIGN ON (safe, rule R6)
20:35:58  session        session type OPERATOR ID field on SIGN ON value {{secret:fakebank/operator_id}}
20:35:58  session        policy allow: type PASSWORD field on SIGN ON (safe, rule R6)
20:35:58  session        session type PASSWORD field on SIGN ON value {{secret:fakebank/operator_password}}
20:35:58  session        policy allow: click SIGN ON button (safe, rule R3)
20:35:58  session        session click SIGN ON button
20:35:58  session        sign_on: screen main_menu → continue
20:35:58  session        signed on (credentials typed by code from the profile; never shown to any model)
20:35:58  replay         policy allow: click link "MEMBER INQUIRY" (main frame) (safe, rule R6)
20:35:58  replay         replay click link "MEMBER INQUIRY" (main frame)
20:35:58  replay         click_member_inquiry_link: screen member_inquiry → continue
20:35:59  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:35:59  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:35:59  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:35:59  replay         replay click button "GO" (main frame)
20:35:59  replay         click_go_button: screen member_not_found → MEMBER_NOT_FOUND
20:35:59  system         result: business_outcome MEMBER_NOT_FOUND
```
