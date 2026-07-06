import feedparser
import requests


def fetch_feed(url):
    """
    Downloads and parses one RSS feed.
    Returns a list of simple dictionaries: headline, link, published date.
    """
    feed = feedparser.parse(url)

    entries = []
    for entry in feed.entries:
        entries.append({
            "headline": entry.get("title", "No title"),
            "link": entry.get("link", ""),
            "published": entry.get("published", "Unknown date")
        })
    return entries


# Keywords that make an article "relevant" to our accessibility news tab
RELEVANT_KEYWORDS = [
    "wcag", "accessibility", "section 508", "ada", "eaa",
    "aria", "screen reader"
]


def is_relevant(entry):
    """
    Checks if an article's headline mentions any accessibility-related keyword.
    Case-insensitive.
    """
    text = entry["headline"].lower()
    return any(keyword in text for keyword in RELEVANT_KEYWORDS)


def filter_relevant(entries):
    """
    Takes a list of articles, returns only the ones that pass is_relevant().
    """
    return [e for e in entries if is_relevant(e)]


def fetch_courtlistener_cases(query="web accessibility WCAG", max_results=5):
    """
    Searches CourtListener's free API for court cases matching the query.
    Returns a list of simple dictionaries: headline, link, published date.
    """
    url = "https://www.courtlistener.com/api/rest/v4/search/"
    params = {
        "q": query,
        "order_by": "dateFiled desc"
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()  # crashes loudly if something went wrong
    data = response.json()

    entries = []
    for result in data.get("results", [])[:max_results]:
        entries.append({
            "headline": result.get("caseName", "Unnamed case"),
            "link": f"https://www.courtlistener.com{result.get('absolute_url', '')}",
            "published": result.get("dateFiled", "Unknown date")
        })
    return entries


# --- Quick manual test ---
if __name__ == "__main__":
    W3C_BLOG_FEED = "https://www.w3.org/blog/news/feed/"
    all_results = fetch_feed(W3C_BLOG_FEED)
    relevant_results = filter_relevant(all_results)

    print(f"Found {len(all_results)} total articles.")
    print(f"{len(relevant_results)} are accessibility-relevant.\n")

    for item in relevant_results:
        print(f"- {item['headline']}  ({item['published']})")

    print("\n--- CourtListener cases ---")
    # No filter_relevant() here — the search query already narrowed this
    # to accessibility-related cases. Case names (e.g. "Thurston v. Omni Hotels")
    # never contain words like "accessibility", so applying a headline-style
    # keyword filter here would wrongly discard every real case.
    cases = fetch_courtlistener_cases()
    for case in cases:
        print(f"- {case['headline']}  ({case['published']})")