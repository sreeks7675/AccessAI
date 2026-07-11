from playwright.sync_api import sync_playwright

AXE_CORE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"


def run_axe_on_url(url):
    """
    Opens a URL in a real (headless) browser, injects axe-core,
    and runs an accessibility scan on it.

    Returns axe-core's raw results dictionary, which includes:
    - 'violations': list of accessibility issues found
    - 'passes': list of checks that passed
    - 'incomplete': checks that need manual review
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        # Load axe-core's JS library into the page
        page.add_script_tag(url=AXE_CORE_CDN_URL)

        # Run axe-core inside the browser and get results back into Python
        results = page.evaluate("async () => await axe.run()")

        browser.close()
        return results


def count_violations(axe_results):
    """
    Takes axe-core's raw results and returns a breakdown by impact level,
    plus the total. This matters for Impact Scoring later (design doc §9) —
    a page with 1 'critical' violation is more urgent than a page with
    5 'minor' ones, even though the raw count looks worse for the second page.
    """
    violations = axe_results.get("violations", [])

    breakdown = {
        "critical": 0,
        "serious": 0,
        "moderate": 0,
        "minor": 0
    }

    for v in violations:
        impact = v.get("impact", "minor")  # axe-core sometimes omits this; default safely
        if impact in breakdown:
            breakdown[impact] += 1

    breakdown["total"] = len(violations)
    return breakdown

# --- Quick manual test ---
if __name__ == "__main__":
    test_urls = [
        "https://www.w3.org/WAI/demos/bad/",
        "https://www.india.gov.in"
    ]

    for url in test_urls:
        print(f"\n{'='*50}")
        print(f"Running axe-core on: {url}")
        try:
            results = run_axe_on_url(url)
            breakdown = count_violations(results)

            print(f"Total violations: {breakdown['total']}")
            print(f"  Critical: {breakdown['critical']}")
            print(f"  Serious:  {breakdown['serious']}")
            print(f"  Moderate: {breakdown['moderate']}")
            print(f"  Minor:    {breakdown['minor']}\n")

            for v in results["violations"]:
                print(f"- [{v['impact']}] {v['id']}: {v['description']}")
        except Exception as e:
            print(f"Failed to scan {url}: {e}")