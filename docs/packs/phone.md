# Crissa phone module pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the optional phone module (the User 2026-10-17: the companion web app ships inside the kit): Crissa on
your phone's browser, served by this install on port 8226 on your own Wi-Fi - `scripts/phone-app-server.py`, the web
app in `display/phone-app/`, the pack format (`lib/phone_pack.py`) and the mouth clock (`lib/phone_sheet.py`). Each
line is said by the install's own voice through its house-speak in render-only mode, so nothing plays on the
computer's speakers. Her phone loops are media in the library download (`<state home>/phone/packs`). How to start it,
let the phone in and add her to the home screen: `modules/phone/README.md`. Standard library only; no login, never
open the port to the internet.

Built from ref `716beb4d8a995f084b663af56d27ea08cb17c96e` by `scripts/crissa-export.py`.
