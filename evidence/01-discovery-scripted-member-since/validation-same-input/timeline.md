# val_20260915_155241_d2f8

- **kind:** validation
- **subject:** fakebank.member.get_member_since@1.1.0
- **status:** success
- **duration:** 1.1 s
- **LLM:** 0 calls · 0 tokens · $0.0000

```
20:52:41  system         validation fakebank.member.get_member_since@1.1.0 (unattended) · no model in the loop
20:52:41  replay         policy allow: click link "MEMBER INQ" (nav frame) (safe, rule R6)
20:52:41  replay         replay click link "MEMBER INQ" (nav frame)
20:52:41  replay         click_member_inq_link: screen member_inquiry → continue
20:52:41  replay         policy allow: type textbox "MEMBER #" (main frame) (safe, rule R6)
20:52:41  replay         replay type textbox "MEMBER #" (main frame) value {{member_number}}
20:52:41  replay         policy allow: click button "GO" (main frame) (safe, rule R4)
20:52:41  replay         replay click button "GO" (main frame)
20:52:42  replay         click_go_button: screen member_detail → continue
20:52:42  replay         extracted member_since (date): 2011-03-14
20:52:42  system         result: success
```
