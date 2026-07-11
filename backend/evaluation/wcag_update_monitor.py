import hashlib
import json
import os
import requests

# Where we remember the last-seen state, so we can detect changes next time
STATE_FILE = os.path.join(os.path.dirname(__file__), "wcag_monitor_state.json")

SOURCES_TO_WATCH = {
    "wcag22_spec": "https://www.w3.org/TR/WCAG22/",
    "wcag3_draft": "https://www.w3.org/TR/wcag-3.0/",
}

GITHUB_RELEASES_URL = "https://api.github.com/repos/w3c/wcag/releases"


def fetch_page_hash(url):
    """
    Downloads a page and returns a short fingerprint (SHA256 hash) of its content.
    If the page changes even slightly, this hash will be completely different.
    If it stays the same, the hash stays the same.
    """
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    content_bytes = response.content
    return hashlib.sha256(content_bytes).hexdigest()


def fetch_latest_github_release():
    """
    Asks GitHub's API for the most recent release of the WCAG spec repo.
    Returns the release tag name (e.g. "wcag22") and its publish date,
    or None if there are no releases.
    """
    response = requests.get(GITHUB_RELEASES_URL, timeout=10)
    response.raise_for_status()
    releases = response.json()

    if not releases:
        return None

    latest = releases[0]  # GitHub returns newest first
    return {
        "tag": latest.get("tag_name", "unknown"),
        "published_at": latest.get("published_at", "unknown")
    }


def load_previous_state():
    """
    Loads what we saw last time we checked. Returns an empty dict
    if this is the very first run (no state file yet).
    """
    if not os.path.exists(STATE_FILE):
        return {}

    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_current_state(state):
    """
    Saves what we saw this time, so next run can compare against it.
    """
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_for_updates():
    """
    The main function. Checks every source we watch, compares against
    what we saw last time, and returns a list of changes detected.

    IMPORTANT (design doc §7.2, Step 2): this function only DETECTS and
    REPORTS changes. It never modifies audit rules automatically — a
    human must review every change before it's approved.
    """
    previous_state = load_previous_state()
    current_state = {}
    changes_detected = []

    # Check each webpage we watch
    # Check each webpage we watch
    for name, url in SOURCES_TO_WATCH.items():
        try:
            current_hash = fetch_page_hash(url)
        except requests.RequestException as e:
            changes_detected.append(
                f"[ERROR] Couldn't check '{name}': {e}"
            )
            continue

        current_state[name] = current_hash

        previous_hash = previous_state.get(name)

        if previous_hash is None:
            changes_detected.append(
                f"[NEW] First time checking '{name}' ({url})"
            )
        elif previous_hash != current_hash:
            changes_detected.append(
                f"[CHANGED] '{name}' content has changed ({url}) — needs human review"
            )

    # Check GitHub releases
    latest_release = fetch_latest_github_release()
    if latest_release:
        current_state["github_latest_release"] = latest_release["tag"]
        previous_release = previous_state.get("github_latest_release")

        if previous_release is None:
            changes_detected.append(f"[NEW] First time checking GitHub releases (latest: {latest_release['tag']})")
        elif previous_release != latest_release["tag"]:
            changes_detected.append(
                f"[CHANGED] New GitHub release detected: {latest_release['tag']} "
                f"(published {latest_release['published_at']}) — needs human review"
            )

    save_current_state(current_state)
    return changes_detected


# --- Quick manual test ---
if __name__ == "__main__":
    print("Checking for WCAG guideline updates...\n")
    changes = check_for_updates()

    if not changes:
        print("No changes detected since last check.")
    else:
        for change in changes:
            print(change)