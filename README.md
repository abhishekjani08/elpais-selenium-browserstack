# El País Opinion Scraper — Selenium + BrowserStack

A Python + Selenium script that scrapes the **Opinion** section of the Spanish
news site [El País](https://elpais.com/), processes the content, and runs
cross-browser on **BrowserStack**.

It demonstrates web scraping, API integration, text processing, and parallel
cross-browser testing.

## What it does

1. Opens the El País **Opinion** section (in Spanish) and dismisses the cookie consent banner.
2. Fetches the **first 5 articles**, printing each **title and content in Spanish**, and downloading each **cover image** to `images/`.
3. Translates the 5 titles **to English** using a translation API (RapidAPI).
4. Finds words that appear **more than twice** across all translated titles and prints each word with its count.
5. Runs the whole solution **locally** first, then on **BrowserStack across 5 parallel sessions** (3 desktop + 2 mobile).

## Project structure

```
elpais-selenium-browserstack/
├── main.py            # entry point (local run / BrowserStack run)
├── pipeline.py        # scrape + translate + analyze logic (driver-agnostic)
├── test_translate.py  # quick standalone check for the translation API
├── requirements.txt
├── .env.example       # template for credentials (copy to .env)
├── .gitignore         # keeps .env and images/ out of git
└── images/            # downloaded cover images (created at runtime)
```

## Prerequisites

- Python 3.10+
- Google Chrome (for the local run — Selenium 4 auto-manages the driver)
- A **BrowserStack** account (Automate) → Username + Access Key
- A **RapidAPI** account, subscribed to a translation API (free tier is fine)

## Setup

```bash
# 1. (optional) virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate       # macOS / Linux

# 2. install dependencies
python -m pip install -r requirements.txt

# 3. add your credentials
copy .env.example .env         # Windows  (cp on macOS/Linux)
# then edit .env with your real values
```

Your `.env` should look like:

```
BROWSERSTACK_USERNAME=your_username
BROWSERSTACK_ACCESS_KEY=your_access_key
RAPIDAPI_KEY=your_rapidapi_key
RAPIDAPI_HOST=rapid-translate-multi-traduction.p.rapidapi.com
```

> The default translation API is **Rapid Translate Multi Traduction** on RapidAPI.
> Subscribe to it (free tier), or change `RAPIDAPI_HOST` if you use a different one.

## Running

**Verify the translation API first** (fastest thing to check):

```bash
python test_translate.py
```

**Run locally** (opens Chrome, runs once):

```bash
python main.py
```

**Run on BrowserStack** (5 parallel sessions, desktop + mobile):

```bash
python main.py --browserstack
```

Watch the sessions live on your BrowserStack Automate dashboard.

## Notes

- **"Repeated more than twice"** is interpreted as **3 or more occurrences**. All
  words are counted, including common ones like *the* (the task does not exclude them).
- The **translation step** uses a **free-tier RapidAPI** plan that has a **daily
  quota**. The script auto-retries with exponential backoff on HTTP 429, but if the
  daily quota is fully used it logs the limit and leaves those titles blank until the
  quota resets the next day. Use your own RapidAPI key in `.env`.
- The **cookie-consent** handling and the **article selectors** in `pipeline.py`
  are the parts most sensitive to site markup changes; they use fallbacks and log
  what they find so any needed tweak is quick to spot.
- Credentials are loaded from `.env` and never hardcoded or committed.