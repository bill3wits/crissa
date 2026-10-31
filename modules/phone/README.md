# Crissa on your phone (optional)

Her face and voice on your phone's browser, served by the Crissa install on your computer. The phone only shows
what the computer sends: her loops, pre-painted with every mouth shape, and each line in her voice with its mouth
timing. The phone renders nothing and needs no app store.

## What you need

- Crissa installed and running on a computer, with her voice working (the Wall speaks).
- Your phone on **the same Wi-Fi** as that computer. The phone app is for your home network only: it has no login,
  so never open its port to the internet.
- The phone packs (her loops for the phone). They come in the library download and unpack to
  `~/.crissa/phone/packs`.
- `segno`, which draws the QR code your phone scans. It is in `requirements.txt`, and Crissa's launcher installs it
  into `.venv` by itself when it is missing. Without it you type the address instead.

## Start it

    python3 scripts/phone-app-server.py

It serves on port **8226**. Chat answers through the agent you set up in Connect; to use another, pass
`--agent <OpenAI-compatible base URL> --model <name>`. `--packs <folder>` points it at packs kept somewhere else.

## Let your phone in

If your computer has a firewall, allow port 8226 from your home network only. On Ubuntu with ufw, for a network
of `192.168.0.x` (put your own network's first three numbers; Crissa's menu shows the exact line under Your phone):

    sudo ufw allow from 192.168.0.0/24 to any port 8226 proto tcp comment 'crissa phone, LAN only'

On a Mac, allow incoming connections for Python when macOS asks.

## Open her on the phone

1. On the computer, open Crissa's menu and choose **Your phone**. It shows a QR code.
2. Scan the code with the phone's camera and open the link. Crissa opens on the phone, connected to this computer.
   The code works once and for 3 minutes; open **Your phone** again for a fresh one.
3. Android: the menu, then **Add to Home screen**; her face is the icon. iPhone: keep her in Safari. A Home Screen
   copy on an iPhone keeps its own cookies, so it opens without the pairing and cannot get in.

Only a phone that came from the code gets in. The server is paired: any other device on your Wi-Fi gets "not
connected" and cannot chat, make her speak or move her, and this computer always gets in. To let a phone back in,
scan a fresh code. A phone you no longer want in: **Disconnect** on the phone.

No QR code on the card? It says why. Usually `segno` is missing: run `.venv/bin/pip install segno==1.6.6` and open
**Your phone** again. (`CRISSA_PHONE_PAIRING=0` or `--no-pairing` turns pairing off: then any device on your Wi-Fi
that opens the address can tap **Use your Crissa at …** and use her. Only do that on a network you trust.)

**Chat** asks your agent and she says the answer. **Say** has her say what you type. **Sync** moves her mouth
earlier or later if your phone's speaker runs late. Left alone she looks down at her phone now and then and comes
back up; tap the text box and she looks up at you.

## Files

- `scripts/phone-app-server.py`: the server (the app, the packs, her lines).
- `display/phone-app/`: the web app.
- `lib/phone_pack.py`: the pack format (one video per clip, its manifest and poster).
- `lib/phone_sheet.py`: puts a line's mouth sheet on the clock of its audio.

Credits for the phone media: `CREDITS.md`.
