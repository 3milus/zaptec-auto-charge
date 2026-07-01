"""
Checks a Zaptec charger and resumes charging if a car is plugged in, not
yet charging, and its battery is below the target percentage. Intended to
be run nightly via a scheduled GitHub Actions workflow.

Required environment variables:
  ZAPTEC_USERNAME    - ZapCloud login email
  ZAPTEC_PASSWORD    - ZapCloud login password
  HYUNDAI_USERNAME   - Bluelink (MyHyundai) login email
  HYUNDAI_PASSWORD   - Bluelink (MyHyundai) login password
Optional:
  ZAPTEC_CHARGER_ID     - UUID of the charger to control. If omitted, the
                          script will use the charger automatically if your
                          account has exactly one; otherwise it lists the
                          available IDs and exits with an error.
  HYUNDAI_VEHICLE_ID    - ID of the car to check. If omitted, the script will
                          use the car automatically if your account has
                          exactly one; otherwise it lists the available IDs
                          and exits with an error.
  TARGET_BATTERY_PERCENT - Only start charging if the car's battery is below
                          this percentage. Defaults to 80.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from hyundai_kia_connect_api import VehicleManager

API_BASE = "https://api.zaptec.com/api"
TOKEN_URL = "https://api.zaptec.com/oauth/token"

STATE_OPERATING_MODE = 710
STATE_FINAL_STOP_ACTIVE = 718

MODE_DISCONNECTED = 1
MODE_REQUESTING = 2
MODE_CHARGING = 3
MODE_CONNECTED_FINISHED = 5

COMMAND_RESUME_CHARGING = 507

HYUNDAI_REGION_EUROPE = 1
HYUNDAI_BRAND_HYUNDAI = 2
DEFAULT_TARGET_BATTERY_PERCENT = 80

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")


def log(message: str) -> None:
    timestamp = datetime.now(LOCAL_TZ).isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", flush=True)


def get_access_token(username: str, password: str) -> str:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": "openid",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def resolve_charger_id(session: requests.Session, configured_id: str | None) -> str:
    if configured_id:
        return configured_id

    response = session.get(f"{API_BASE}/chargers", timeout=30)
    response.raise_for_status()
    chargers = response.json().get("data", [])

    if len(chargers) == 1:
        return chargers[0]["id"]

    ids = ", ".join(f"{c.get('name')} ({c.get('id')})" for c in chargers)
    raise RuntimeError(
        "Could not auto-select a charger. Set ZAPTEC_CHARGER_ID to one of: "
        f"{ids or '(none found)'}"
    )


def get_charger_state(session: requests.Session, charger_id: str) -> dict[int, str]:
    response = session.get(f"{API_BASE}/chargers/{charger_id}/state", timeout=30)
    response.raise_for_status()
    return {row["stateId"]: row.get("valueAsString") for row in response.json()}


def resume_charging(session: requests.Session, charger_id: str) -> None:
    response = session.post(
        f"{API_BASE}/chargers/{charger_id}/sendCommand/{COMMAND_RESUME_CHARGING}",
        timeout=30,
    )
    response.raise_for_status()


def get_car_battery_percentage(
    username: str, password: str, configured_vehicle_id: str | None
) -> int:
    manager = VehicleManager(
        region=HYUNDAI_REGION_EUROPE,
        brand=HYUNDAI_BRAND_HYUNDAI,
        username=username,
        password=password,
        pin="",
    )
    manager.check_and_refresh_token()

    if configured_vehicle_id:
        vehicle_id = configured_vehicle_id
    elif len(manager.vehicles) == 1:
        vehicle_id = next(iter(manager.vehicles))
    else:
        ids = ", ".join(f"{v.name} ({v.id})" for v in manager.vehicles.values())
        raise RuntimeError(
            "Could not auto-select a Hyundai vehicle. Set HYUNDAI_VEHICLE_ID "
            f"to one of: {ids or '(none found)'}"
        )

    manager.update_vehicle_with_cached_state(vehicle_id)
    return manager.get_vehicle(vehicle_id).ev_battery_percentage


def main() -> int:
    username = os.environ["ZAPTEC_USERNAME"]
    password = os.environ["ZAPTEC_PASSWORD"]
    configured_charger_id = os.environ.get("ZAPTEC_CHARGER_ID")

    hyundai_username = os.environ["HYUNDAI_USERNAME"]
    hyundai_password = os.environ["HYUNDAI_PASSWORD"]
    configured_vehicle_id = os.environ.get("HYUNDAI_VEHICLE_ID")
    target_battery_percent = int(
        os.environ.get("TARGET_BATTERY_PERCENT") or DEFAULT_TARGET_BATTERY_PERCENT
    )

    log("Authenticating with ZapCloud...")
    token = get_access_token(username, password)

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    charger_id = resolve_charger_id(session, configured_charger_id)
    log(f"Using charger {charger_id}")

    state = get_charger_state(session, charger_id)
    mode = int(state.get(STATE_OPERATING_MODE, -1))
    final_stop_active = state.get(STATE_FINAL_STOP_ACTIVE) == "1"

    if mode == MODE_DISCONNECTED:
        log("No vehicle connected. Nothing to do.")
    elif mode == MODE_CHARGING:
        log("Already charging. Nothing to do.")
    elif mode == MODE_REQUESTING or (mode == MODE_CONNECTED_FINISHED and final_stop_active):
        log("Vehicle connected but not charging. Checking battery state via Bluelink...")
        try:
            battery_percent = get_car_battery_percentage(
                hyundai_username, hyundai_password, configured_vehicle_id
            )
        except Exception as exc:  # noqa: BLE001 - Bluelink is best-effort, fail open
            log(f"Could not reach Bluelink ({exc}). Approving charge anyway.")
            resume_charging(session, charger_id)
            log("Resume command sent.")
        else:
            log(f"Car battery at {battery_percent}%.")
            if battery_percent < target_battery_percent:
                log(f"Below target of {target_battery_percent}%. Sending resume command...")
                resume_charging(session, charger_id)
                log("Resume command sent.")
            else:
                log(f"Already at or above target of {target_battery_percent}%. Not starting charge.")
    else:
        log(f"Unhandled charger mode {mode} (finalStopActive={final_stop_active}). Nothing to do.")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - surface any failure in Actions logs
        log(f"ERROR: {exc}")
        sys.exit(1)
