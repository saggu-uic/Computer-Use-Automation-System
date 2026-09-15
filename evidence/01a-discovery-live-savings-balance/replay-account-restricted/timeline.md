# rep_20260915_121958_ed4c

- **kind:** replay
- **subject:** fakebank.member.get_regular_savings_balance@1.0.0
- **status:** business_outcome · `ACCOUNT_RESTRICTED`
- **duration:** 1.3 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
17:19:58  system         replay fakebank.member.get_regular_savings_balance@1.0.0 (unattended) · no model in the loop
17:19:58  session        policy allow: type OPERATOR ID field on SIGN ON (safe, rule R6)
17:19:58  session        session type OPERATOR ID field on SIGN ON value {{secret:fakebank/operator_id}}
17:19:58  session        policy allow: type PASSWORD field on SIGN ON (safe, rule R6)
17:19:58  session        session type PASSWORD field on SIGN ON value {{secret:fakebank/operator_password}}
17:19:58  session        policy allow: click SIGN ON button (safe, rule R3)
17:19:58  session        session click SIGN ON button
17:19:58  session        sign_on: screen main_menu → continue
17:19:58  session        signed on (credentials typed by code from the profile; never shown to any model)
17:19:58  replay         policy allow: click link "MEMBER INQUIRY" (main frame) (safe, rule R6)
17:19:58  replay         replay click link "MEMBER INQUIRY" (main frame)
17:19:58  replay         click_member_inquiry_link: screen member_inquiry → continue
17:19:58  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
17:19:58  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
17:19:59  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
17:19:59  replay         replay click button "GO" (main frame)
17:19:59  replay         click_go_button: screen access_restricted → ACCOUNT_RESTRICTED
17:19:59  system         result: business_outcome ACCOUNT_RESTRICTED
```
