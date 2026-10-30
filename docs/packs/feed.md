# Crissa feed pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the wall's home feed, `scripts/voiceonly-home-server.py` on 127.0.0.1:8099: the server the Tk wall
reads for its chips (`/v1/house`), the beam clock (`/v1/sound`), the speaker (`/v1/speaker`), the camera cell
(`/frame`), Register (`/v1/register`), the shared glass prefs (`/v1/glass-prefs`), the keys (`/v1/action`), the
logs roll and the Control Panel page (`/panel`). The launcher (`scripts/crissa.py`) starts it as the child `feed`
and points the wall at it. Its rows are the launcher's own children and the add-on tiles under `~/.crissa/panel.d`;
Init, the agent framework's rows and the Render Studio are the reference install's and answer a small mark here.

Built from ref `3827c939b0da73183097dc8bc36b675a5f452887` by `scripts/crissa-export.py`.
