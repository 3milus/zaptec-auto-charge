# Zaptec Auto Charge

Runs every night at 22:00 Danish time via GitHub Actions. If your Ioniq 5 is
plugged into the charger, not yet charging, and its battery is below a
target percentage, it sends a resume command so you don't have to remember
to start it in the Zaptec app.

## How it works

- A GitHub Actions scheduled workflow (`.github/workflows/auto-charge.yml`)
  fires at both 20:00 and 21:00 UTC every day, since GitHub cron only
  understands UTC and Denmark alternates between UTC+1 (CET) and UTC+2
  (CEST). The script checks the real Copenhagen clock and only acts on the
  run that actually lands at 22:00 local time; the other run is a no-op.
- The script logs into ZapCloud, reads the charger's state, and:
  - does nothing if no car is connected,
  - does nothing if it's already charging,
  - if a car is connected but charging hasn't started, it logs into
    Bluelink (MyHyundai) and reads the car's battery percentage from the
    cloud's cached state (no forced wake-up of the car, so it doesn't drain
    the 12V battery), then sends the "resume charging" command (507) only if
    the battery is below `TARGET_BATTERY_PERCENT` (default 80).
  - if Bluelink can't be reached at all (login failure, API error, etc.),
    the script fails open: it approves and starts charging anyway rather
    than leaving the car unplugged-but-idle overnight. Check the run logs
    if this happens, since it means the battery threshold wasn't honored
    for that night.

## One-time setup

1. **Create the GitHub repo** (private recommended) and push this folder to it.
2. **Add repository secrets** — GitHub repo -> Settings -> Secrets and
   variables -> Actions -> New repository secret:
   - `ZAPTEC_USERNAME` — your ZapCloud/Zaptec app login email
   - `ZAPTEC_PASSWORD` — your ZapCloud/Zaptec app login password
   - `ZAPTEC_CHARGER_ID` — optional. Only needed if your account has more
     than one charger. Leave it unset first; if the script can't
     auto-select a charger it will list the available IDs in the run log so
     you can copy the right one in.
   - `HYUNDAI_USERNAME` — your MyHyundai/Bluelink app login email
   - `HYUNDAI_PASSWORD` — your MyHyundai/Bluelink app login password
   - `HYUNDAI_VEHICLE_ID` — optional. Only needed if your Bluelink account
     has more than one car. Leave it unset first; if the script can't
     auto-select a vehicle it will list the available IDs in the run log.
   - Also add a repository **variable** (not secret, since it's not
     sensitive) named `TARGET_BATTERY_PERCENT` if you want something other
     than the default of 80.
3. **Test it manually** — GitHub repo -> Actions tab -> "Zaptec Auto Charge"
   -> "Run workflow". This runs immediately regardless of the time of day
   (the 22:00-only check only applies to the two scheduled cron triggers,
   not to manual runs) so you can verify it works end to end. Check the run
   logs for what it decided to do.
4. If a scheduled run fails, GitHub emails the repo owner by default, so
   you'll find out if something breaks.

## Known limitation

Zaptec's public API docs only document command 507 ("resume charging") as
resuming a *paused* session (mode 5 with `FinalStopActive`), not explicitly
as starting a brand-new unauthorized session (mode 2, "connected -
requesting to charge"). This script sends 507 in both cases since it's the
only documented start/resume command. Watch the first few nightly run logs
to confirm it actually starts your charging session as expected — if it
doesn't, let me know what the logs show and we'll adjust.

Battery state comes from the unofficial [hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api)
library (no official public Bluelink API exists). It's community-maintained
and Hyundai occasionally changes their backend in ways that break it — if a
run fails with a Bluelink login/API error, check that project's GitHub
issues before assuming it's something in this script.
