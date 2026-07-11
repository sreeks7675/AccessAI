def summarize_with_llm(prompt):
    """
    Sends a prompt to an LLM and returns its raw text response.

    This is the ONLY function that knows which LLM provider we're using.
    Everything else in this file just calls summarize_with_llm() and
    doesn't care whether it's Mistral (local), Ollama, or an API.

    TODO: Replace this placeholder once the team confirms:
    - Local Mistral-7B-Instruct-v0.3 (via Ollama, vLLM, or transformers)
    - Or a hosted API instead

    For now, it returns a fake response so the rest of the pipeline
    can be built and tested without a working LLM connection yet.
    """
    # PLACEHOLDER — replace this line when the LLM connection is ready
    return "PLACEHOLDER_SUMMARY: LLM not yet connected."


def build_summary_prompt(article):
    """
    Builds the instruction we'll send to the LLM for one article.
    Takes an article dict (headline, link, published) and asks for:
    - a 3-sentence summary
    - 3 key takeaways
    - which WCAG criteria / regulations are implicated

    Matches the design doc's requirement (§8.3): every summary must state
    (1) which regulation was implicated, (2) what the failure was,
    (3) what the outcome was.
    """
    return f"""You are summarising a web accessibility news article or legal case for a report.

Headline: {article['headline']}
Link: {article['link']}

Write:
1. A 3-sentence summary covering: which regulation was implicated, what the accessibility failure was, and what the outcome was.
2. Exactly 3 key takeaways, each one short sentence.
3. Any WCAG Success Criteria numbers mentioned or implied (e.g. 1.4.3), or "None identified" if none apply.

Respond in this exact format:
SUMMARY: <3 sentences>
TAKEAWAYS:
- <takeaway 1>
- <takeaway 2>
- <takeaway 3>
WCAG_CRITERIA: <comma-separated list or "None identified">
"""


def summarize_article(article):
    """
    Takes one article dict, returns it with an added 'ai_summary' field
    containing the raw LLM output (unparsed for now).
    """
    prompt = build_summary_prompt(article)
    raw_response = summarize_with_llm(prompt)

    article_with_summary = article.copy()
    article_with_summary["ai_summary"] = raw_response
    return article_with_summary


# --- Quick manual test ---
if __name__ == "__main__":
    test_article = {
        "headline": "Juan Carlos Gil v. Winn-Dixie Stores, Inc.",
        "link": "https://www.courtlistener.com/example",
        "published": "2021-04-07"
    }

    result = summarize_article(test_article)
    print("Original headline:", result["headline"])
    print("AI Summary:", result["ai_summary"])