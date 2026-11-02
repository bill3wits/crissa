# Crissa Control Panel pack

Created with Grok.

## Licences and credits

- `LICENSE`: the MIT licence for the source code in this pack.
- `CREDITS.md`: who and what made her, starting with Grok Imagine.
- `THIRD-PARTY-NOTICES.md`: third-party code and the tools used to build the data.

It carries the Control Panel: one page (`display/crissa-panel.html`, served by
`scripts/crissa-panel-server.py` on 127.0.0.1:8097) that shows every service of this install, restarts,
starts and stops them, reads their journal and unit script, and carries All OS Off / Turn On All Services
(`scripts/crissa-os-power`) with an optional Park GPU step. It finds its services by its own rules (units named
`crissa-*`, or run from this folder, or named in its unit notes); it ships no list of anyone's services.
Python standard library and systemd; no GPU, nothing to install.

    python3 scripts/crissa-panel-server.py            # then open http://127.0.0.1:8097/
    python3 scripts/crissa-panel-server.py --unit     # prints a systemd user unit for it

Settings: `config/crissa-panel.json`, then `<state>/panel.json` (`~/.crissa`; `CRISSA_HOME` moves it).
`CRISSA_PORT_PANEL` or `CRISSA_PORT_OFFSET` moves the port.

Built from ref `716beb4d8a995f084b663af56d27ea08cb17c96e` by `scripts/crissa-export.py`.
