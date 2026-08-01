"""
Entry point.

  python main.py                 -> run once, locally, in your own Chrome
  python main.py --browserstack  -> run across 5 parallel browsers on BrowserStack
                                    (mix of desktop + mobile)

Credentials are read from a .env file (see .env.example). Nothing is hardcoded.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from selenium import webdriver

from pipeline import run_pipeline

load_dotenv()

# BrowserStack Automate hub. Credentials are embedded in the URL at runtime.
HUB_URL = "https://{user}:{key}@hub-cloud.browserstack.com/wd/hub"

# The 5 parallel targets: 3 desktop + 2 real mobile devices.
# Device names / versions can be tweaked using BrowserStack's capability generator
# if any specific combination isn't currently available on your plan.
CAPABILITIES = [
    {
        "browserName": "Chrome",
        "browserVersion": "latest",
        "bstack:options": {"os": "Windows", "osVersion": "11", "sessionName": "Win11-Chrome"},
    },
    {
        "browserName": "Edge",
        "browserVersion": "latest",
        "bstack:options": {"os": "Windows", "osVersion": "11", "sessionName": "Win11-Edge"},
    },
    {
        "browserName": "Safari",
        "bstack:options": {"os": "OS X", "osVersion": "Sonoma", "sessionName": "macOS-Safari"},
    },
    {
        "browserName": "chrome",
        "bstack:options": {
            "deviceName": "Samsung Galaxy S23",
            "osVersion": "13.0",
            "realMobile": True,
            "sessionName": "Android-GalaxyS23",
        },
    },
    {
        "browserName": "safari",
        "bstack:options": {
            "deviceName": "iPhone 15",
            "osVersion": "17",
            "realMobile": True,
            "sessionName": "iOS-iPhone15",
        },
    },
]


# --------------------------------------------------------------------------- #
# LOCAL
# --------------------------------------------------------------------------- #
def run_local():
    """Selenium 4 auto-downloads the matching chromedriver, so no manual driver.
    We also reduce Chrome's automation fingerprint, because news sites like
    El Pais often throttle or block obviously-automated browsers."""
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    # Mask navigator.webdriver so the page doesn't see an obvious automation flag.
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
    except Exception:
        pass

    try:
        run_pipeline(driver, session_label="local", print_full=True)
    finally:
        driver.quit()


# --------------------------------------------------------------------------- #
# BROWSERSTACK (5 parallel threads)
# --------------------------------------------------------------------------- #
def _build_options(cap):
    name = cap["browserName"].lower()
    if name == "safari":
        options = webdriver.SafariOptions()
    elif name == "firefox":
        options = webdriver.FirefoxOptions()
    else:
        options = webdriver.ChromeOptions()

    options.set_capability("browserName", cap["browserName"])
    if cap.get("browserVersion"):
        options.set_capability("browserVersion", cap["browserVersion"])

    bstack = dict(cap["bstack:options"])
    bstack.setdefault("buildName", "elpais-selenium-browserstack")
    bstack.setdefault("projectName", "El Pais CE Assignment")
    options.set_capability("bstack:options", bstack)
    return options


def _print_session_link(driver, label):
    """Print this session's shareable BrowserStack URLs.

    Read from the live session via browserstack_executor rather than the REST
    API, because the REST API is not enabled on free plans. `public_url` is the
    link that can be shared with someone who is not logged in.
    """
    try:
        details = json.loads(
            driver.execute_script('browserstack_executor: {"action":"getSessionDetails"}')
        )
        print(f"[{label}] public link  : {details.get('public_url')}")
        print(f"[{label}] dashboard    : {details.get('browser_url')}")
    except Exception as e:
        print(f"[{label}] could not read session details: {e}")


def _run_one(cap):
    user = os.getenv("BROWSERSTACK_USERNAME")
    key = os.getenv("BROWSERSTACK_ACCESS_KEY")
    hub = HUB_URL.format(user=user, key=key)
    label = cap["bstack:options"].get("sessionName", cap["browserName"])

    driver = None
    try:
        driver = webdriver.Remote(command_executor=hub, options=_build_options(cap))
        run_pipeline(driver, session_label=label, print_full=False)
        driver.execute_script(
            'browserstack_executor: {"action":"setSessionStatus",'
            '"arguments":{"status":"passed","reason":"pipeline completed"}}'
        )
        _print_session_link(driver, label)
        return (label, "PASSED")
    except Exception as e:
        print(f"[{label}] ERROR: {e}")
        try:
            if driver:
                driver.execute_script(
                    'browserstack_executor: {"action":"setSessionStatus",'
                    '"arguments":{"status":"failed","reason":"pipeline error"}}'
                )
        except Exception:
            pass
        return (label, f"FAILED: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass  # BrowserStack sometimes closes the session right after setSessionStatus


def run_browserstack():
    if not os.getenv("BROWSERSTACK_USERNAME") or not os.getenv("BROWSERSTACK_ACCESS_KEY"):
        raise SystemExit("Set BROWSERSTACK_USERNAME and BROWSERSTACK_ACCESS_KEY in .env first.")

    print("Launching 5 parallel BrowserStack sessions (3 desktop + 2 mobile)...\n")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_run_one, cap) for cap in CAPABILITIES]
        for fut in as_completed(futures):
            label, status = fut.result()
            print(f"[RESULT] {label}: {status}")


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="El Pais Opinion scraper (Selenium + BrowserStack)")
    parser.add_argument(
        "--browserstack",
        action="store_true",
        help="run on BrowserStack across 5 parallel browsers instead of locally",
    )
    args = parser.parse_args()

    if args.browserstack:
        run_browserstack()
    else:
        run_local()