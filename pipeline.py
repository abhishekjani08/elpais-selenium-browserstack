"""
Core pipeline for the El Pais Opinion assignment.

This module is *driver-agnostic*: it receives an already-created Selenium
WebDriver (either a local Chrome driver or a remote BrowserStack driver) and
runs the full flow on it:

  1. Open the Opinion section (in Spanish) and grab the first 5 articles.
  2. For each article: print title + content (Spanish) and download the cover image.
  3. Translate the 5 titles to English via a RapidAPI translation API.
  4. Count words that appear more than twice across all translated titles.

Keeping the logic here means main.py can call run_pipeline() the exact same
way for a single local run and for each of the 5 parallel BrowserStack runs.
"""

import os
import re
import time
from collections import Counter

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

OPINION_URL = "https://elpais.com/opinion/"


# --------------------------------------------------------------------------- #
# 1. SCRAPING
# --------------------------------------------------------------------------- #
def accept_cookies(driver, timeout=5):
    """
    El Pais shows a GDPR cookie consent banner (Didomi) that blocks clicks until
    dismissed. We try a couple of strategies and continue quietly if none appear.
    Once accepted, the cookie persists for the session, so detail pages can pass a
    short timeout. NOTE: this selector is the most sensitive to site markup changes.
    """
    strategies = [
        (By.ID, "didomi-notice-agree-button"),
        (By.XPATH, "//button[contains(., 'Aceptar')]"),
    ]
    for by, sel in strategies:
        try:
            btn = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            print("  [consent] cookie banner dismissed")
            return True
        except Exception:
            continue
    return False


def get_opinion_articles(driver, n=5):
    """Return the first `n` articles as [{'title':..., 'url':...}, ...]."""
    driver.get(OPINION_URL)
    accept_cookies(driver, timeout=8)

    # Confirm the page is in Spanish (assignment requirement).
    try:
        lang = driver.find_element(By.TAG_NAME, "html").get_attribute("lang")
        print(f"  [lang] <html lang='{lang}'>  (expected 'es')")
    except Exception:
        pass

    # Wait until article cards are present, then read them.
    WebDriverWait(driver, 20).until(
        EC.presence_of_all_elements_located((By.TAG_NAME, "article"))
    )
    cards = driver.find_elements(By.TAG_NAME, "article")

    items = []
    for card in cards:
        title, url = None, None
        # El Pais headlines are usually an <a> inside an <h2>.
        try:
            a = card.find_element(By.CSS_SELECTOR, "h2 a")
            title, url = a.text.strip(), a.get_attribute("href")
        except Exception:
            # Fallback: first anchor in the card.
            try:
                a = card.find_element(By.CSS_SELECTOR, "a")
                title, url = a.text.strip(), a.get_attribute("href")
            except Exception:
                pass
        if title and url and url.startswith("http"):
            items.append({"title": title, "url": url})
        if len(items) >= n:
            break

    return items[:n]


def fetch_article_detail(driver, url):
    """Open one article and return (content_text, cover_image_url)."""
    driver.get(url)
    accept_cookies(driver, timeout=2)  # cookie usually already set; quick check

    body_selectors = [
        "div[data-dtm-region='articulo_cuerpo'] p",
        "div.a_c p",
        "article p",
    ]

    # IMPORTANT: wait for the body to actually render. El Pais hydrates the article
    # text a moment AFTER the page finishes loading, so querying immediately can
    # miss it on fast sessions -- that is what left content empty on some browsers.
    try:
        WebDriverWait(driver, 15).until(
            lambda d: any(d.find_elements(By.CSS_SELECTOR, s) for s in body_selectors)
        )
    except Exception:
        pass  # subscriber-gated pieces may have no free preview paragraphs at all

    content = ""
    for sel in body_selectors:
        paras = driver.find_elements(By.CSS_SELECTOR, sel)
        texts = [p.text.strip() for p in paras if p.text.strip()]
        if texts:
            content = "\n".join(texts)
            break

    # ---- cover image: scroll it into view and poll, since images are lazy-loaded
    image_url = None
    for sel in ["figure img", "article img"]:
        imgs = driver.find_elements(By.CSS_SELECTOR, sel)
        if not imgs:
            continue
        img = imgs[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", img)
        except Exception:
            pass
        candidate = None
        for _ in range(10):  # up to ~3s for the real src to swap in
            candidate = img.get_attribute("src") or img.get_attribute("data-src")
            srcset = img.get_attribute("srcset")
            if (not candidate or candidate.startswith("data:")) and srcset:
                candidate = srcset.split(",")[-1].strip().split(" ")[0]
            if candidate and candidate.startswith("http"):
                break
            time.sleep(0.3)
        if candidate and candidate.startswith("http"):
            image_url = candidate
            break

    return content, image_url


def download_image(image_url, folder, filename_base):
    """Download the cover image to `folder`. Returns the path or None."""
    if not image_url:
        print("    [image] none found for this article")
        return None
    try:
        os.makedirs(folder, exist_ok=True)
        ext = os.path.splitext(image_url.split("?")[0])[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", filename_base)[:60]
        path = os.path.join(folder, f"{safe}{ext}")
        resp = requests.get(
            image_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)
        print(f"    [image] saved -> {path}")
        return path
    except Exception as e:
        print(f"    [image] download failed: {e}")
        return None


# --------------------------------------------------------------------------- #
# 2. TRANSLATION  (RapidAPI - Rapid Translate Multi Traduction)
# --------------------------------------------------------------------------- #
def translate_to_english(text, debug=False, max_retries=4):
    """
    Translate a single Spanish string to English via RapidAPI, with automatic
    backoff on HTTP 429 (the free tier is rate-limited).

    Defaults to the 'Rapid Translate Multi Traduction' API:
      host: rapid-translate-multi-traduction.p.rapidapi.com
      POST /t  with JSON {"from":"es","to":"en","q":"<text>"}
      -> typically returns a JSON list like ["<translated>"]

    If you subscribed to a different API, change RAPIDAPI_HOST in .env and, if
    its response shape differs, the parsing block below.
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    host = os.getenv("RAPIDAPI_HOST", "rapid-translate-multi-traduction.p.rapidapi.com")
    url = f"https://{host}/t"
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": host,
        "Content-Type": "application/json",
    }
    payload = {"from": "es", "to": "en", "q": text}

    backoff = 2.0
    for attempt in range(1, max_retries + 1):
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if debug and attempt == 1:
            print(f"  [translate] status={resp.status_code} raw={resp.text[:200]}")

        # Free tier is rate-limited: on 429, wait and retry instead of failing.
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else backoff
            print(f"  [translate] 429 rate-limited; waiting {wait:.0f}s "
                  f"(attempt {attempt}/{max_retries})")
            time.sleep(wait)
            backoff *= 2  # exponential backoff
            continue

        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            return str(data[0])
        if isinstance(data, dict):
            for key in ("translatedText", "translation", "text", "result"):
                if key in data:
                    return str(data[key])
        return str(data)

    raise RuntimeError("translation still rate-limited (429) after retries")


def translate_titles(titles, pause=1.5):
    """Translate a list of titles ES->EN. A short pause between calls keeps us
    under the free-tier rate limit; translate_to_english also backs off on 429.
    The raw response of the first call is printed so any schema mismatch shows."""
    out = []
    for i, t in enumerate(titles):
        if i > 0:
            time.sleep(pause)  # stay under the per-second/minute free-tier limit
        try:
            en = translate_to_english(t, debug=(i == 0))
        except Exception as e:
            print(f"  [translate] failed for title {i + 1}: {e}")
            en = ""
        out.append(en)
    return out


# --------------------------------------------------------------------------- #
# 3. ANALYSIS
# --------------------------------------------------------------------------- #
def repeated_words(headers_en, min_count=3):
    """
    Count words appearing MORE THAN TWICE (i.e. 3+ times) across all headers.
    Words are lowercased and stripped of punctuation. All words are counted,
    including common ones like 'the' (the assignment does not exclude them).
    """
    words = []
    for h in headers_en:
        words.extend(re.findall(r"[a-zA-Z']+", h.lower()))
    counts = Counter(words)
    return {w: c for w, c in counts.items() if c >= min_count}


# --------------------------------------------------------------------------- #
# ORCHESTRATION
# --------------------------------------------------------------------------- #
def run_pipeline(driver, session_label="local", image_dir="images", print_full=True):
    """Run the whole flow on a given driver. `session_label` tags all output
    and image filenames so parallel BrowserStack runs stay distinguishable."""
    print(f"\n===== [{session_label}] START =====")

    articles = get_opinion_articles(driver, n=5)
    print(f"[{session_label}] found {len(articles)} articles")

    titles_es = []
    for idx, art in enumerate(articles, 1):
        content, image_url = fetch_article_detail(driver, art["url"])
        titles_es.append(art["title"])

        print(f"\n--- [{session_label}] Article {idx} (Spanish) ---")
        print(f"Title  : {art['title']}")
        if print_full:
            print(f"Content:\n{content if content else '(no content extracted)'}")
        else:
            snippet = content[:400] + ("..." if len(content) > 400 else "")
            print(f"Content: {snippet if snippet else '(no content extracted)'}")

        download_image(image_url, image_dir, f"{session_label}_article{idx}")

    print(f"\n[{session_label}] Translating {len(titles_es)} titles ES -> EN ...")
    titles_en = translate_titles(titles_es)

    print(f"\n[{session_label}] Translated headers (English):")
    for i, (es, en) in enumerate(zip(titles_es, titles_en), 1):
        print(f"  {i}. {en}    (from: {es})")

    reps = repeated_words(titles_en, min_count=3)
    print(f"\n[{session_label}] Words repeated more than twice across headers:")
    if reps:
        for w, c in sorted(reps.items(), key=lambda kv: -kv[1]):
            print(f"  '{w}': {c}")
    else:
        print("  (none - expected with only 5 short headers)")

    print(f"===== [{session_label}] DONE =====\n")
    return {"titles_es": titles_es, "titles_en": titles_en, "repeated": reps}