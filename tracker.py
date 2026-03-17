#!/usr/bin/env python3
"""
XICE Shareholder Tracker
========================
Tracks daily changes in the top 20 shareholders of companies listed on
Nasdaq Iceland's Main Market (XICE).

Uses requests for static HTML sites, falls back to Selenium (headless Chrome)
for sites that render shareholder data via JavaScript.

Usage:
  python tracker.py                  # Run a single scan
  python tracker.py --schedule       # Run daily at 08:30 UTC (= IST) on weekdays
  python tracker.py --dashboard      # Only regenerate the dashboard
  python tracker.py --test-email     # Send a test email
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import smtplib
import schedule
from pathlib import Path
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional

import yaml
import requests
from bs4 import BeautifulSoup

# Selenium imports — used for JS-heavy sites
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DASHBOARD_DIR = BASE_DIR / "dashboard"
CONFIG_PATH = BASE_DIR / "config.yaml"
LOG_PATH = BASE_DIR / "tracker.log"

DATA_DIR.mkdir(exist_ok=True)
SNAPSHOTS_DIR.mkdir(exist_ok=True)
DASHBOARD_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("xice-tracker")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    log.warning("config.yaml not found - using defaults (email disabled)")
    return {}


# ---------------------------------------------------------------------------
# XICE Company Registry — March 2026
# needs_js: True = site renders data via JavaScript, use Selenium
# ---------------------------------------------------------------------------
XICE_COMPANIES = [
    {"ticker": "ALVO",   "name": "Alvotech hf.",                              "shareholder_url": "https://investors.alvotech.com/shareholders",                                      "needs_js": True,  "data_source": "morningstar"},
    {"ticker": "AMRQ",   "name": "Amaroq Minerals Ltd.",                      "shareholder_url": "https://www.amaroqminerals.com/investors/shareholders/",                            "needs_js": False},
    {"ticker": "ARION",  "name": "Arion banki hf.",                           "shareholder_url": "https://www.arionbanki.is/bankinn/fjarfestar/hlutabref/hluthafalisti/",              "needs_js": True,  "max_shareholders": 50},
    {"ticker": "BERA",   "name": u"Bera (Ölgerðin Egill Skallagrímsson hf.)", "shareholder_url": "https://www.olgerdin.is/fjarfestar/hluthafaupplysingar/",                          "needs_js": True},
    {"ticker": "BRIM",   "name": "Brim hf.",                                  "shareholder_url": "https://www.brim.is/is/fjarfestar/hluthafaupplysingar",                              "needs_js": False},
    {"ticker": "EIK",    "name": u"Eik fasteignafélag hf.",                   "shareholder_url": "https://www.eik.is/hluthafar",                                                      "needs_js": True},
    {"ticker": "EIM",    "name": u"Eimskipafélag Íslands hf.",                "shareholder_url": "https://www.eimskip.com/investors/share-information/",                                "needs_js": True,  "scraper": "keldan_iframe"},
    {"ticker": "FESTI",  "name": "Festi hf.",                                 "shareholder_url": "https://www.festi.is/hluthafaupplysingar",                                           "needs_js": True},
    {"ticker": "HAGA",   "name": "Hagar hf.",                                 "shareholder_url": "https://www.hagar.is/fjarfestar/hluthafaupplysingar/hluthafalisti/",                  "needs_js": False},
    {"ticker": "HAMP",   "name": u"Hampiðjan hf.",                            "shareholder_url": "https://hampidjan.is/fjarmal/hluthafar/",                                            "needs_js": False},
    {"ticker": "HEIMAR", "name": "Heimar hf.",                                "shareholder_url": "https://www.heimar.is/fjarfestar/adrar-upplysingar/staerstu-hluthafar/",              "needs_js": True},
    {"ticker": "ICEAIR", "name": "Icelandair Group hf.",                      "shareholder_url": "https://www.icelandairgroup.com/investors",                                          "needs_js": True,  "scraper": "livemarket", "livemarket_tab": "Shareholders list"},
    {"ticker": "ICESEA", "name": "Iceland Seafood International hf.",          "shareholder_url": "https://icelandseafood.com/investors/shareholders/",                                 "needs_js": True,  "scraper": "livemarket"},
    {"ticker": "ISB",    "name": u"Íslandsbanki hf.",                         "shareholder_url": "https://www.islandsbanki.is/is/grein/hluthafar",                                     "needs_js": True,  "max_shareholders": 50},
    {"ticker": "ISF",    "name": u"Ísfélag hf.",                              "shareholder_url": "https://isfelag.is/fjarfestar/",                                                     "needs_js": True,  "scraper": "livemarket"},
    {"ticker": "JBTM",   "name": "JBT Marel Corporation",                     "shareholder_url": None,                                                                                 "needs_js": False, "data_source": "morningstar"},
    {"ticker": "KALD",   "name": u"Kaldalón hf.",                             "shareholder_url": "https://kaldalon.is/fjarfestar/",                                                    "needs_js": True,  "scraper": "parallel_columns"},
    {"ticker": "KVIKA",  "name": "Kvika banki hf.",                           "shareholder_url": "https://kvika.is/fjarfestaupplysingar/?category=shareholderCatalog",                  "needs_js": False, "max_shareholders": 50},
    {"ticker": "NOVA",   "name": "Nova hf.",                                  "shareholder_url": "https://www.nova.is/baksvids/hluthafar",                                             "needs_js": False},
    {"ticker": "OCS",    "name": "Oculis Holding AG",                         "shareholder_url": None,                                                                                 "needs_js": False, "data_source": "morningstar"},
    {"ticker": "REITIR", "name": u"Reitir fasteignafélag hf.",                "shareholder_url": "https://www.reitir.is/fjarfestar/hluthafaupplysingar",                                "needs_js": True},
    {"ticker": "SIMINN", "name": u"Síminn hf.",                               "shareholder_url": "https://www.siminn.is/fjarfestar/hluthafar-og-hlutabref",                             "needs_js": True},
    {"ticker": "SJOVA",  "name": u"Sjóvá-Almennar tryggingar hf.",            "shareholder_url": "https://www.sjova.is/sjova/upplysingagjof/fjarfestar/hluthafalisti",                  "needs_js": True},
    {"ticker": "SKAGI",  "name": "Skagi hf.",                                 "shareholder_url": "https://skagi.is/hluthafar",                                                         "needs_js": True},
    {"ticker": "SKEL",   "name": u"Skel fjárfestingafélag hf.",               "shareholder_url": "https://skel.is/hluthafar/staerstu-hluthafar",                                       "needs_js": True,  "scraper": "livemarket"},
    {"ticker": "SVN",    "name": u"Síldarvinnslan hf.",                       "shareholder_url": "https://svn.is/fjarfestar/",                                                         "needs_js": True,  "scraper": "livemarket"},
    {"ticker": "SYN",    "name": u"Sýn hf.",                                  "shareholder_url": "https://www.syn.is/fjarfestatengsl/staerstu-hluthafar",                               "needs_js": False},
]


# ---------------------------------------------------------------------------
# Selenium Browser
# ---------------------------------------------------------------------------
_driver = None

def get_driver():
    """Create or return a shared headless Chrome instance."""
    global _driver
    if _driver is not None:
        return _driver

    if not SELENIUM_AVAILABLE:
        log.error("Selenium not installed. Run: pip install selenium webdriver-manager")
        return None

    log.info("Starting headless Chrome...")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=is,en")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        service = Service(ChromeDriverManager().install())
        _driver = webdriver.Chrome(service=service, options=opts)
        _driver.set_page_load_timeout(45)
        log.info("Headless Chrome ready.")
        return _driver
    except Exception as e:
        log.error(f"Failed to start Chrome: {e}")
        return None


def close_driver():
    global _driver
    if _driver:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


def fetch_with_selenium(url: str, wait_seconds: int = 10) -> Optional[str]:
    """Fetch a page using headless Chrome, waiting for JS to render."""
    driver = get_driver()
    if not driver:
        return None

    try:
        driver.get(url)
        time.sleep(wait_seconds)

        # Wait for shareholder-like content to appear
        try:
            WebDriverWait(driver, 12).until(
                lambda d: d.find_elements(By.TAG_NAME, "table") or
                          "%" in d.page_source
            )
        except Exception:
            pass

        # Scroll down to trigger lazy-loaded content
        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
        except Exception:
            pass

        return driver.page_source
    except Exception as e:
        log.warning(f"Selenium failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# HTTP Fetching
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,is;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_page(url: str, timeout: int = 30) -> Optional[str]:
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status in (404, 403, 410):
                log.warning(f"Permanent error {status} for {url}, not retrying.")
                return None
            log.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2 * (attempt + 1))
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt+1} failed for {url}: {e}")
            time.sleep(2 * (attempt + 1))
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_percentage(text: str) -> Optional[float]:
    text = text.strip().replace("%", "").replace(" ", "").replace("\xa0", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        val = float(text)
        return round(val, 2) if 0 < val < 100 else None
    except (ValueError, TypeError):
        return None


def parse_share_count(text: str) -> int:
    text = text.strip().replace(" ", "").replace("\xa0", "")
    cleaned = re.sub(r"[.,](?=\d{3})", "", text)
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return 0


def parse_shareholders_from_html(html: str, ticker: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    def _is_distribution_table(rows_data: list[list[str]]) -> bool:
        """Detect tables that show holding-size distribution, not individual shareholders.
        E.g. ICEAIR: '1 - 1 000 000 | 6.03% | ...' """
        range_pattern = re.compile(r"\d[\d\s.,]*-\s*\d[\d\s.,]*|unknown|total", re.IGNORECASE)
        range_count = 0
        for texts in rows_data:
            if any(range_pattern.search(t) for t in texts):
                range_count += 1
        return range_count > len(rows_data) * 0.4 and len(rows_data) < 12

    # Keywords for header rows (column labels — no percentage present)
    header_kw = ["shareholder", "hluthaf", "nafn", "eigandi",
                 "owner distribution", "dreifing eigenda", "name"]

    # Keywords for footer/summary rows — checked against the FIRST cell only
    footer_kw = ["samtals", "total", "alls", "eigin hlut", "treasury",
                 "subtotal", "number of", u"fjöldi", "issued shares",
                 u"útistandandi", "issued sdr"]

    # Catch-all "other shareholders" entries to exclude
    other_kw = [u"aðrir hluthafar", u"aðrir", "other shareholders", "others"]

    def _is_header_row(texts: list[str]) -> bool:
        lower = " ".join(t.lower() for t in texts)
        return any(kw in lower for kw in header_kw) and not any(parse_percentage(t) is not None for t in texts)

    def _is_footer_row(texts: list[str]) -> bool:
        """Check if the first (name) cell is a summary/total label.
        Only triggers on short cells or cells that start with the keyword,
        to avoid false positives on shareholder names containing these words."""
        if not texts:
            return False
        first = texts[0].strip()
        first_lower = first.lower()
        # Short cell that matches a footer keyword — definitely a summary row
        if len(first) <= 25:
            return any(kw in first_lower for kw in footer_kw)
        # Longer cell — only match if it starts with the keyword
        return any(first_lower.startswith(kw) for kw in footer_kw)

    def _is_other_shareholders(name: str) -> bool:
        """Filter out 'Aðrir hluthafar' / 'Other shareholders' catch-all rows."""
        lower = name.strip().lower()
        return any(kw == lower or lower.startswith(kw) for kw in other_kw)

    def _extract_row(texts: list[str]) -> Optional[dict]:
        """Try to extract (name, pct, shares) from a list of cell texts."""
        name = None
        pct = None
        shares = 0
        for text in texts:
            t = text.strip()
            # Skip pure rank numbers (1, 2, 3...)
            if re.match(r"^\d{1,3}\.?$", t):
                continue
            # Try percentage
            p = parse_percentage(t)
            if p is not None and pct is None:
                pct = p
                continue
            # Try share count (pure numeric, >1000)
            if re.match(r"^[\d.,\s\xa0]+$", t) and len(t.strip()) > 3:
                s = parse_share_count(t)
                if s > 1000:
                    shares = s
                    continue
            # Otherwise treat as name candidate (at least 3 chars, not pure number)
            if len(t) > 2 and name is None and not re.match(r"^[\d.,\s%]+$", t):
                name = t
        if name and pct and pct > 0 and not _is_other_shareholders(name):
            # Filter out ISIN codes (e.g. IS0000034734) — these are securities, not shareholders
            if re.match(r"^[A-Z]{2}\d{8,12}$", name):
                return None
            return {"name": name, "shares": shares, "pct": pct}
        return None

    def _cell_text(cell) -> str:
        """Extract visible text from a table cell, ignoring responsive-hidden children.
        Many Icelandic sites (e.g. Reitir) embed mobile-only duplicates inside
        the name cell using 'sm:hidden' / 'md:hidden' / 'lg:hidden' Tailwind classes
        or inline 'display:none'. Stripping these prevents concatenation artifacts
        like 'Gildi - lífeyrissjóðurFjöldi hluta130.609.960Hlutfall18.739%'."""
        from copy import copy
        cell_copy = copy(cell)
        # Remove elements hidden via Tailwind responsive classes
        for hidden in cell_copy.find_all(class_=re.compile(r"(^|\s)(sm:|md:|lg:|xl:|-)hidden(\s|$)")):
            hidden.decompose()
        # Remove elements hidden via inline style
        for hidden in cell_copy.find_all(style=re.compile(r"display\s*:\s*none")):
            hidden.decompose()
        return cell_copy.get_text(strip=True)

    def _parse_table(table) -> list[dict]:
        """Parse a single <table> element into shareholder entries."""
        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        all_row_texts = []
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                all_row_texts.append([_cell_text(c) for c in cells])

        if _is_distribution_table(all_row_texts):
            return []

        results = []
        for texts in all_row_texts:
            if _is_header_row(texts):
                continue
            if _is_footer_row(texts):
                continue
            extracted = _extract_row(texts)
            if extracted:
                extracted["rank"] = len(results) + 1
                results.append(extracted)
        return results

    # ---- Strategy 1: HTML tables ----
    # Parse ALL tables and collect results. Some sites (e.g. SIMINN) split
    # shareholders across multiple side-by-side tables (10 + 10).
    all_table_results = []
    for table in soup.find_all("table"):
        results = _parse_table(table)
        if results:
            all_table_results.append(results)

    if all_table_results:
        # If we have multiple tables of similar structure, try merging them
        # (SIMINN: two tables with 10 shareholders each, side by side)
        if len(all_table_results) >= 2:
            # Only consider tables with 3+ shareholder rows for merging
            substantial = [t for t in all_table_results if len(t) >= 3]
            biggest = max(all_table_results, key=len)

            if len(substantial) >= 2:
                # Check if substantial tables are similar size (merge candidates)
                sizes = sorted([len(t) for t in substantial])
                if sizes[0] >= sizes[-1] * 0.3:
                    merged = []
                    for tbl in substantial:
                        merged.extend(tbl)
                    # De-duplicate by name (in case tables overlap)
                    seen = set()
                    deduped = []
                    for s in merged:
                        if s["name"] not in seen:
                            seen.add(s["name"])
                            deduped.append(s)
                    # Re-rank
                    for i, s in enumerate(deduped):
                        s["rank"] = i + 1
                    if len(deduped) > len(biggest):
                        return deduped[:20]

        # Otherwise return the single largest table
        best = max(all_table_results, key=len)
        return best[:20]

    # ---- Strategy 2: Div/list-based structures ----
    # Many Icelandic sites (Modular Finance widgets) render shareholders in
    # <div> grids rather than <table>. Look for repeated sibling elements
    # that each contain a name-like string + percentage.
    div_results = []
    for container in soup.find_all(["div", "ul", "ol", "section"]):
        children = container.find_all(recursive=False)
        if len(children) < 3:
            continue
        # Check if children share a common tag
        tags = [c.name for c in children]
        if len(set(tags)) > 2:
            continue

        # --- Approach A: Each child has both name + percentage ---
        candidates = []
        for child in children:
            text_parts = [t.strip() for t in child.stripped_strings if len(t.strip()) > 1]
            if len(text_parts) < 2:
                continue
            extracted = _extract_row(text_parts)
            if extracted:
                extracted["rank"] = len(candidates) + 1
                candidates.append(extracted)

        if len(candidates) >= 3 and len(candidates) > len(div_results):
            div_results = candidates

        # --- Approach B: Alternating rows (name in one, data in the next) ---
        # Sites like NOVA render each shareholder across two sibling divs:
        #   <div>Birta lífeyrissjóður</div>
        #   <div>426.719.059  12%</div>
        # Try pairing consecutive children and merging their text parts.
        if len(candidates) < 3:
            paired_candidates = []
            child_texts = []
            for child in children:
                parts = [t.strip() for t in child.stripped_strings if len(t.strip()) > 1]
                child_texts.append(parts)

            i = 0
            while i < len(child_texts) - 1:
                merged = child_texts[i] + child_texts[i + 1]
                extracted = _extract_row(merged)
                if extracted:
                    extracted["rank"] = len(paired_candidates) + 1
                    paired_candidates.append(extracted)
                    i += 2  # Skip the pair
                else:
                    i += 1

            if len(paired_candidates) >= 3 and len(paired_candidates) > len(div_results):
                div_results = paired_candidates

    if div_results:
        return div_results[:20]

    # ---- Strategy 3: Text patterns (last resort) ----
    page_text = soup.get_text("\n", strip=True)
    shareholders = []
    pattern = re.compile(r"^(.{3,60}?)\s+([\d.,]+)\s*%", re.MULTILINE)
    for name_raw, pct_raw in pattern.findall(page_text):
        name = name_raw.strip().rstrip(".")
        pct = parse_percentage(pct_raw + "%")
        if pct and 0 < pct < 100 and len(name) > 2:
            lower_name = name.lower()
            if any(kw in lower_name for kw in ["shareholder", "hluthaf", "total",
                                                 "samtals", "distribution", "dreifing",
                                                 "unknown", u"óþekkt"]):
                continue
            if _is_other_shareholders(name):
                continue
            shareholders.append({"name": name, "shares": 0, "pct": pct, "rank": len(shareholders) + 1})

    return shareholders[:20]


# ---------------------------------------------------------------------------
# Specialized Scrapers
# ---------------------------------------------------------------------------

def scrape_livemarketdata_widget(ticker: str, url: str, tab_name: str = None) -> list[dict]:
    """Scrape sites using the <shareholders-large-v2> LiveMarket/Keldan web component.
    These render inside a shadow DOM that page_source can't see.
    We use Selenium JS to extract the shadow DOM innerHTML, which contains
    a standard HTML table with shareholder data.

    If tab_name is provided, clicks that tab inside the shadow DOM first
    (e.g. ICEAIR needs 'Shareholders list' tab clicked)."""
    driver = get_driver()
    if not driver:
        return []

    try:
        driver.get(url)
        time.sleep(12)

        # If a tab needs to be clicked first (e.g. ICEAIR "Shareholders list")
        if tab_name:
            js_click_tab = f"""
            var w = document.querySelector('shareholders-large-v2');
            if (w && w.shadowRoot) {{
                var links = w.shadowRoot.querySelectorAll('a, button, span, div');
                for (var el of links) {{
                    if (el.textContent.trim() === '{tab_name}') {{
                        el.click();
                        return true;
                    }}
                }}
                // Also try case-insensitive partial match
                for (var el of links) {{
                    if (el.textContent.trim().toLowerCase().includes('{tab_name.lower()}')) {{
                        el.click();
                        return true;
                    }}
                }}
            }}
            return false;
            """
            clicked = driver.execute_script(js_click_tab)
            if clicked:
                log.info(f"  -> Clicked '{tab_name}' tab for {ticker}")
                time.sleep(4)  # Wait for tab content to load
            else:
                log.warning(f"  -> Could not find '{tab_name}' tab for {ticker}")

        # Extract shadow DOM innerHTML
        js = (
            "var w = document.querySelector('shareholders-large-v2');"
            "if (w && w.shadowRoot) { return w.shadowRoot.innerHTML; }"
            "return '';"
        )
        inner_html = driver.execute_script(js)

        if inner_html and len(inner_html) > 100:
            log.info(f"  -> Got {len(inner_html):,} chars from LiveMarket shadow DOM for {ticker}")
            shareholders = parse_shareholders_from_html(inner_html, ticker)
            if shareholders:
                return shareholders
            else:
                log.warning(f"  -> Shadow DOM HTML found but parsing returned 0 for {ticker}")
        else:
            log.warning(f"  -> No shadow DOM content found for {ticker} (widget may not have loaded)")

    except Exception as e:
        log.warning(f"LiveMarket widget scrape failed for {ticker}: {e}")

    return []


def scrape_keldan_iframe(ticker: str) -> list[dict]:
    """Scrape the Keldan/LiveMarket iframe directly."""
    iframe_url = f"https://lmd.keldan.is/ir/shareholders/{ticker}"
    driver = get_driver()
    if not driver:
        return []

    try:
        driver.get(iframe_url)
        time.sleep(6)

        html = driver.page_source
        if html:
            shareholders = parse_shareholders_from_html(html, ticker)
            if shareholders:
                log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (Keldan iframe)")
                return shareholders

    except Exception as e:
        log.warning(f"Keldan iframe scrape failed for {ticker}: {e}")

    return []


def scrape_parallel_columns(html: str, ticker: str) -> list[dict]:
    """Parse sites that render names and percentages in parallel div columns.
    Used by KALD (Elementor jet-listing-dynamic-repeater pattern)."""
    soup = BeautifulSoup(html, "html.parser")

    # Look for Elementor/JetEngine repeater containers
    repeaters = soup.find_all("div", class_="jet-listing-dynamic-repeater__items")
    if len(repeaters) < 2:
        return []

    # Extract text from each repeater
    repeater_data = []
    for rep in repeaters:
        items = rep.find_all("div", class_="jet-listing-dynamic-repeater__item")
        texts = [item.get_text(strip=True) for item in items]
        repeater_data.append(texts)

    # Find the name column (mostly non-numeric, long strings) and pct column (has %)
    name_col = None
    pct_col = None
    for i, texts in enumerate(repeater_data):
        if not texts:
            continue
        pct_count = sum(1 for t in texts if parse_percentage(t) is not None)
        name_count = sum(1 for t in texts if len(t) > 3 and not re.match(r"^[\d.,\s%]+$", t))

        if pct_count > len(texts) * 0.6 and pct_col is None:
            pct_col = i
        elif name_count > len(texts) * 0.6 and name_col is None:
            name_col = i

    if name_col is None or pct_col is None:
        return []

    names = repeater_data[name_col]
    pcts = repeater_data[pct_col]
    min_len = min(len(names), len(pcts))

    shareholders = []
    for i in range(min_len):
        name = names[i].strip()
        pct = parse_percentage(pcts[i])
        if not name or not pct:
            continue
        lower = name.lower()
        if any(kw in lower for kw in [u"aðrir hluthafar", u"aðrir", "other shareholders",
                                       "samtals", "total", u"fjöldi"]):
            continue
        shareholders.append({"name": name, "shares": 0, "pct": pct, "rank": len(shareholders) + 1})

    return shareholders[:20]


# ---------------------------------------------------------------------------
# Scraping Orchestration
# ---------------------------------------------------------------------------
def scrape_company(company: dict) -> list[dict]:
    ticker = company["ticker"]
    url = company["shareholder_url"]
    needs_js = company.get("needs_js", False)
    scraper = company.get("scraper")
    max_sh = company.get("max_shareholders", 20)

    if not url:
        log.info(f"Skipping {ticker} ({company['name']}) - no URL")
        return []

    if company.get("skip_reason"):
        log.info(f"Skipping {ticker} ({company['name']}) - {company['skip_reason']}")
        return []

    log.info(f"Scraping {ticker} ({company['name']})")

    def _cap(results):
        """Apply per-company shareholder cap and re-rank."""
        capped = results[:max_sh]
        for i, s in enumerate(capped):
            s["rank"] = i + 1
        return capped

    # ---- Specialized scrapers (run first if configured) ----

    if scraper == "keldan_iframe":
        # Go directly to the Keldan iframe — the company page itself doesn't have the data
        shareholders = scrape_keldan_iframe(ticker)
        if shareholders:
            log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (Keldan iframe)")
            return _cap(shareholders)
        log.warning(f"  -> Keldan iframe returned no data for {ticker}")
        return []

    if scraper == "livemarket":
        # Try shadow DOM piercing on the company page
        tab_name = company.get("livemarket_tab")
        log.info(f"  -> Using LiveMarket widget scraper for {ticker}...")
        shareholders = scrape_livemarketdata_widget(ticker, url, tab_name=tab_name)
        if shareholders:
            log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (LiveMarket widget)")
            return _cap(shareholders)
        # Fallback: try Keldan iframe directly
        log.info(f"  -> LiveMarket widget empty, trying Keldan iframe for {ticker}...")
        shareholders = scrape_keldan_iframe(ticker)
        if shareholders:
            log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (Keldan iframe fallback)")
            return _cap(shareholders)
        log.warning(f"  -> No shareholders found for {ticker} via LiveMarket or Keldan")
        return []

    if scraper == "parallel_columns":
        # Needs Selenium to render the Elementor repeaters
        html = fetch_with_selenium(url)
        if html:
            shareholders = scrape_parallel_columns(html, ticker)
            if shareholders:
                log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (parallel columns)")
                return _cap(shareholders)
        log.warning(f"  -> Parallel columns parser returned no data for {ticker}")
        return []

    # ---- Generic scraper (no special scraper configured) ----

    # Step 1: Try plain HTTP first (fast)
    if not needs_js:
        html = fetch_page(url)
        if html:
            shareholders = parse_shareholders_from_html(html, ticker)
            if shareholders:
                log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (HTTP)")
                return _cap(shareholders)
            else:
                log.info(f"  -> HTTP returned no data for {ticker}, trying Selenium...")
        else:
            log.info(f"  -> HTTP fetch failed for {ticker}, trying Selenium...")

    # Step 2: Use Selenium for JS-heavy sites or as fallback
    html = fetch_with_selenium(url)
    if html:
        shareholders = parse_shareholders_from_html(html, ticker)
        if shareholders:
            log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (Selenium)")
            return _cap(shareholders)

        # Step 3: Auto-detect LiveMarket widget even without scraper hint
        if "<shareholders-large-v2" in html:
            log.info(f"  -> Auto-detected LiveMarket widget for {ticker}, piercing shadow DOM...")
            shareholders = scrape_livemarketdata_widget(ticker, url)
            if shareholders:
                log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (LiveMarket auto)")
                return _cap(shareholders)

        # Step 4: Auto-detect Keldan iframe even without scraper hint
        if "lmd.keldan.is/ir/shareholders" in html:
            log.info(f"  -> Auto-detected Keldan iframe for {ticker}, loading directly...")
            shareholders = scrape_keldan_iframe(ticker)
            if shareholders:
                log.info(f"  -> Found {len(shareholders)} shareholders for {ticker} (Keldan auto)")
                return _cap(shareholders)

        log.warning(f"  -> Selenium loaded page but no shareholders parsed for {ticker}")
    else:
        log.warning(f"  -> Both HTTP and Selenium failed for {ticker}")

    return []


# ---------------------------------------------------------------------------
# Snapshot Storage & Diffing
# ---------------------------------------------------------------------------
def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def save_snapshot(data: dict) -> Path:
    date = today_str()
    path = SNAPSHOTS_DIR / f"{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": date, "companies": data, "timestamp": datetime.now(timezone.utc).isoformat()}, f, indent=2, ensure_ascii=False)
    log.info(f"Snapshot saved: {path}")
    return path


def load_snapshot(date: str) -> Optional[dict]:
    path = SNAPSHOTS_DIR / f"{date}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def get_previous_snapshot() -> Optional[dict]:
    today = today_str()
    for snap_path in sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True):
        if snap_path.stem < today:
            with open(snap_path, "r", encoding="utf-8") as f:
                return json.load(f)
    return None


def diff_shareholders(old, new, ticker, company_name, threshold=0.5):
    old_map = {s["name"]: s for s in old}
    new_map = {s["name"]: s for s in new}
    old_names, new_names = set(old_map), set(new_map)

    entered = [{"name": n, "pct": new_map[n]["pct"], "rank": new_map[n]["rank"]} for n in sorted(new_names - old_names)]
    exited  = [{"name": n, "pct": old_map[n]["pct"], "rank": old_map[n]["rank"]} for n in sorted(old_names - new_names)]

    changed = []
    for name in old_names & new_names:
        delta = round(new_map[name]["pct"] - old_map[name]["pct"], 2)
        if abs(delta) >= threshold:
            changed.append({"name": name, "old_pct": old_map[name]["pct"], "new_pct": new_map[name]["pct"], "delta": delta})
    changed.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {"ticker": ticker, "company": company_name, "entered": entered, "exited": exited, "changed": changed, "has_changes": bool(entered or exited or changed)}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def build_email_html(changes, date):
    has_any = any(c["has_changes"] for c in changes)
    wc = [c for c in changes if c["has_changes"]]
    te = sum(len(c["entered"]) for c in changes)
    tx = sum(len(c["exited"]) for c in changes)
    ts = sum(len(c["changed"]) for c in changes)

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a2e;margin:0;padding:0;background:#f0f2f5}}
.ctr{{max-width:680px;margin:0 auto;background:#fff}}
.hdr{{background:linear-gradient(135deg,#0d1b3e,#1a3a6b);padding:32px 28px}}
.hdr h1{{color:#fff;margin:0;font-size:22px}}.hdr p{{color:#8ba3c7;margin:8px 0 0;font-size:14px}}
.bdy{{padding:24px 28px}}.sum{{background:#f8f9fb;border-radius:8px;padding:16px;margin-bottom:24px;border-left:4px solid #1a3a6b}}
.cs{{margin-bottom:20px;border-bottom:1px solid #eee;padding-bottom:16px}}.cs:last-child{{border-bottom:none}}
.ct{{font-size:16px;font-weight:600;color:#0d1b3e;margin:0 0 10px}}
.tk{{display:inline-block;background:#e8edf3;color:#1a3a6b;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:700;margin-left:6px}}
.cl{{margin:0;padding:0;list-style:none}}.cl li{{padding:6px 0;font-size:14px;border-bottom:1px solid #f5f5f5}}
.b{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:6px}}
.be{{background:#d4edda;color:#155724}}.bx{{background:#f8d7da;color:#721c24}}
.bu{{background:#cce5ff;color:#004085}}.bd{{background:#fff3cd;color:#856404}}
.ftr{{padding:20px 28px;background:#f8f9fb;font-size:12px;color:#888}}
</style></head><body><div class="ctr"><div class="hdr"><h1>XICE Shareholder Changes</h1><p>{date} &mdash; Nasdaq Iceland Main Market</p></div>
<div class="bdy"><div class="sum">"""

    if has_any:
        html += f"<strong>{len(wc)}</strong> companies with changes: <strong>{te}</strong> entries, <strong>{tx}</strong> exits, <strong>{ts}</strong> ownership shifts."
    else:
        html += "<em>No shareholder changes detected today.</em>"
    html += "</div>"

    for r in wc:
        html += f'<div class="cs"><p class="ct">{r["company"]}<span class="tk">{r["ticker"]}</span></p><ul class="cl">'
        for e in r["entered"]:
            html += f'<li><span class="b be">ENTERED</span> <strong>{e["name"]}</strong> at {e["pct"]}% (rank #{e["rank"]})</li>'
        for e in r["exited"]:
            html += f'<li><span class="b bx">EXITED</span> <strong>{e["name"]}</strong> was at {e["pct"]}% (rank #{e["rank"]})</li>'
        for c in r["changed"]:
            d = "bu" if c["delta"] > 0 else "bd"
            a = "&#9650;" if c["delta"] > 0 else "&#9660;"
            html += f'<li><span class="b {d}">{a} {abs(c["delta"])}pp</span> <strong>{c["name"]}</strong> {c["old_pct"]}% &rarr; {c["new_pct"]}%</li>'
        html += "</ul></div>"

    html += '</div><div class="ftr">XICE Shareholder Tracker &mdash; auto-generated. Registries may not reflect beneficial owners.</div></div></body></html>'
    return html


def send_email(subject, html_body, config, attachment_path=None):
    ec = config.get("email", {})
    if not ec.get("smtp_host"):
        log.warning("Email not configured.")
        return False
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = ec["from_address"]
    msg["To"] = ", ".join(ec["recipients"])

    # Email body (plain + HTML alternative)
    body_alt = MIMEMultipart("alternative")
    body_alt.attach(MIMEText("XICE shareholder changes detected. View the HTML version or open the attached dashboard.", "plain"))
    body_alt.attach(MIMEText(html_body, "html"))
    msg.attach(body_alt)

    # Attach dashboard HTML if provided
    if attachment_path and Path(attachment_path).exists():
        try:
            with open(attachment_path, "rb") as f:
                part = MIMEBase("text", "html")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename=\"XICE_Dashboard_{today_str()}.html\"")
                msg.attach(part)
                log.info(f"Attached dashboard: {attachment_path}")
        except Exception as e:
            log.warning(f"Could not attach dashboard: {e}")

    try:
        if ec.get("smtp_port", 587) == 465:
            srv = smtplib.SMTP_SSL(ec["smtp_host"], 465)
        else:
            srv = smtplib.SMTP(ec["smtp_host"], ec.get("smtp_port", 587))
            srv.starttls()
        if ec.get("smtp_user"):
            srv.login(ec["smtp_user"], ec["smtp_password"])
        srv.sendmail(ec["from_address"], ec["recipients"], msg.as_string())
        srv.quit()
        log.info(f"Email sent to {ec['recipients']}")
        return True
    except Exception as e:
        log.error(f"Email failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def generate_dashboard(all_changes, current_data, date):
    # Load full historical snapshots (up to 90 days)
    history = []
    for p in sorted(SNAPSHOTS_DIR.glob("*.json"), reverse=True)[:90]:
        with open(p, encoding="utf-8") as f:
            snap = json.load(f)
            history.append({"date": snap["date"], "companies": snap.get("companies", {})})

    dd = {
        "date": date, "generated_at": datetime.now(timezone.utc).isoformat(),
        "companies": [{"ticker": c["ticker"], "name": c["name"], "shareholders": current_data.get(c["ticker"], []), "count": len(current_data.get(c["ticker"], [])), "data_source": c.get("data_source", "")} for c in XICE_COMPANIES],
        "changes_today": [c for c in all_changes if c["has_changes"]],
        "history": history,
    }

    # Save data.json for local server use
    with open(DASHBOARD_DIR / "data.json", "w", encoding="utf-8") as f:
        json.dump(dd, f, indent=2, ensure_ascii=False)

    # Embed data directly into the HTML so the file is fully self-contained
    embedded_data = json.dumps(dd, ensure_ascii=False)
    html = DASHBOARD_HTML.replace(
        "/*__EMBEDDED_DATA__*/",
        f"var __EMBEDDED_DATA__ = {embedded_data};"
    )
    with open(DASHBOARD_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Dashboard generated: {DASHBOARD_DIR / 'index.html'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_scan():
    config = load_config()
    date = today_str()
    log.info(f"=== XICE Scan {date} ===")

    current_data = {}
    ok = 0
    for company in XICE_COMPANIES:
        try:
            sh = scrape_company(company)
            current_data[company["ticker"]] = sh
            if sh:
                ok += 1
        except Exception as e:
            log.error(f"Error scraping {company['ticker']}: {e}")
            current_data[company["ticker"]] = []
        time.sleep(config.get("request_delay_seconds", 2))

    # Clean up Selenium browser
    close_driver()

    save_snapshot(current_data)

    prev = get_previous_snapshot()
    all_changes = []
    if prev:
        pc = prev.get("companies", {})
        threshold = config.get("change_threshold_pct", 0.5)
        for c in XICE_COMPANIES:
            t = c["ticker"]
            if pc.get(t) and current_data.get(t):
                all_changes.append(diff_shareholders(pc[t], current_data[t], t, c["name"], threshold))
    else:
        log.info("First run - no previous snapshot to diff against.")

    has_changes = any(c["has_changes"] for c in all_changes)

    # Generate dashboard first so we can attach it to the email
    generate_dashboard(all_changes, current_data, date)
    dashboard_path = DASHBOARD_DIR / "index.html"

    if has_changes:
        body = build_email_html(all_changes, date)
        send_email(f"XICE Shareholder Changes - {date}", body, config, attachment_path=dashboard_path)
        with open(DATA_DIR / f"email_{date}.html", "w", encoding="utf-8") as f:
            f.write(body)
    elif all_changes:
        log.info("No changes detected today.")
        if config.get("send_no_change_emails", False):
            body = build_email_html(all_changes, date)
            send_email(f"XICE Daily Report (no changes) - {date}", body, config, attachment_path=dashboard_path)

    log.info(f"=== Done. {ok}/{len(XICE_COMPANIES)} scraped, {sum(1 for c in all_changes if c['has_changes'])} with changes. ===")


def run_scheduler(run_time="08:30"):
    log.info(f"Scheduler started. Weekdays at {run_time} UTC.")
    schedule.every().day.at(run_time).do(run_scan)
    run_scan()
    while True:
        schedule.run_pending()
        time.sleep(60)


DASHBOARD_HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>XICE Shareholder Tracker</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;500;700&family=JetBrains+Mono:wght@400;600&display=swap');
:root{--bg:#0a0e1a;--surface:#111827;--surface-2:#1a2235;--border:#243049;--text:#e2e8f0;--text-muted:#8892a8;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--amber:#f59e0b;--green-bg:rgba(34,197,94,.1);--red-bg:rgba(239,68,68,.1);--amber-bg:rgba(245,158,11,.1)}
*{margin:0;padding:0;box-sizing:border-box}body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
.top-bar{background:linear-gradient(135deg,#0d1b3e,#152447);border-bottom:1px solid var(--border);padding:20px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.logo{display:flex;align-items:center;gap:14px}.logo-mark{width:36px;height:36px;background:var(--accent);border-radius:8px;display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;color:#fff}
.logo h1{font-size:18px;font-weight:700}.logo h1 span{color:var(--accent)}.meta{font-size:13px;color:var(--text-muted);font-family:'JetBrains Mono',monospace}
.container{max-width:1280px;margin:0 auto;padding:28px 32px}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:32px}
.stat-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px}.stat-card .label{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
.stat-card .value{font-size:28px;font-weight:700;font-family:'JetBrains Mono',monospace}.stat-card .value.green{color:var(--green)}.stat-card .value.red{color:var(--red)}.stat-card .value.amber{color:var(--amber)}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:24px;overflow:hidden}
.panel-header{padding:18px 24px;border-bottom:1px solid var(--border);background:var(--surface-2)}.panel-header h2{font-size:15px;font-weight:600}.panel-body{padding:20px 24px}
.change-item{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.04)}.change-item:last-child{border-bottom:none}
.badge{display:inline-flex;align-items:center;padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;min-width:72px;justify-content:center}
.badge-enter{background:var(--green-bg);color:var(--green);border:1px solid rgba(34,197,94,.2)}.badge-exit{background:var(--red-bg);color:var(--red);border:1px solid rgba(239,68,68,.2)}
.badge-up{background:var(--green-bg);color:var(--green);border:1px solid rgba(34,197,94,.2)}.badge-down{background:var(--amber-bg);color:var(--amber);border:1px solid rgba(245,158,11,.2)}
.change-info{flex:1}.change-name{font-weight:500;font-size:14px}.change-detail{font-size:12px;color:var(--text-muted);margin-top:2px}.change-company{font-size:12px;font-family:'JetBrains Mono',monospace;color:var(--accent)}
.company-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-top:8px}
.company-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden;transition:border-color .2s}.company-card:hover{border-color:var(--accent)}
.company-card-header{padding:16px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);background:var(--surface-2);cursor:pointer}
.company-card-header .ticker{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;color:var(--accent)}
.company-card-header .name{font-size:13px;color:var(--text-muted);max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.source-badge{font-size:10px;font-weight:600;color:#e87a1e;background:rgba(232,122,30,0.12);padding:2px 7px;border-radius:4px;margin-left:auto;white-space:nowrap;letter-spacing:0.3px}
.company-card-body{padding:14px 20px}.shareholder-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,.03)}
.shareholder-row:last-child{border-bottom:none}.shareholder-row .rank{color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px;width:24px}
.shareholder-row .sh-name{flex:1;padding:0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;transition:color .15s}.shareholder-row .sh-name:hover{color:var(--accent);text-decoration:underline}.shareholder-row .pct{font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--accent);font-size:12px}
.sh-profile-section{margin-bottom:20px}.sh-profile-section h3{font-size:14px;font-weight:600;margin-bottom:10px;color:var(--text)}.sh-profile-section h3 .sh-section-badge{font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;margin-left:8px;padding:2px 8px;border-radius:4px}
.sh-holding-card{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.sh-holding-left{display:flex;align-items:center;gap:12px}.sh-holding-ticker{font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--accent);font-size:14px;min-width:60px}.sh-holding-name{font-size:13px;color:var(--text-muted)}
.sh-holding-right{display:flex;align-items:center;gap:16px;font-family:'JetBrains Mono',monospace;font-size:13px}.sh-holding-pct{font-weight:700;color:var(--accent)}.sh-holding-rank{color:var(--text-muted);font-size:11px}
.sh-timeline{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}.sh-timeline-dot{font-size:11px;font-family:'JetBrains Mono',monospace;padding:2px 6px;border-radius:4px;background:var(--surface);border:1px solid var(--border)}
.top-movers-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.mover-card{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:14px 18px;display:flex;align-items:center;gap:14px;cursor:pointer;transition:border-color .2s}.mover-card:hover{border-color:var(--accent)}
.mover-rank{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;min-width:32px;text-align:center}
.mover-info{flex:1;min-width:0}.mover-name{font-weight:500;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mover-detail{font-size:12px;color:var(--text-muted);margin-top:2px}
.mover-delta{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:15px;text-align:right;white-space:nowrap}
.mover-delta.up{color:var(--green)}.mover-delta.down{color:var(--amber)}.mover-delta.enter{color:var(--green)}.mover-delta.exit{color:var(--red)}
.export-btn{background:var(--surface);border:1px solid var(--border);color:var(--accent);font-size:13px;padding:9px 18px;border-radius:8px;cursor:pointer;font-family:'DM Sans',sans-serif;font-weight:500;transition:all .15s;display:inline-flex;align-items:center;gap:6px}.export-btn:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.controls{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap;align-items:center}
.no-data{color:var(--text-muted);font-style:italic;font-size:13px;padding:12px 0}
.search-input{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 16px;color:var(--text);font-size:14px;width:300px;outline:none}
.search-input:focus{border-color:var(--accent)}.search-input::placeholder{color:var(--text-muted)}
.section-title{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:16px;font-weight:600}
.empty-state{text-align:center;padding:48px 24px;color:var(--text-muted)}
.hist-btn{background:none;border:1px solid var(--border);color:var(--accent);font-size:11px;padding:3px 10px;border-radius:6px;cursor:pointer;font-family:'JetBrains Mono',monospace}.hist-btn:hover{background:var(--surface-2)}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:200;justify-content:center;align-items:center}
.modal-overlay.active{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:16px;width:90%;max-width:800px;max-height:85vh;overflow:hidden;display:flex;flex-direction:column}
.modal-header{padding:20px 24px;border-bottom:1px solid var(--border);background:var(--surface-2);display:flex;justify-content:space-between;align-items:center}
.modal-header h2{font-size:16px;font-weight:600}.modal-header .ticker-badge{font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--accent);background:rgba(59,130,246,.1);padding:4px 12px;border-radius:6px;font-size:13px}
.modal-close{background:none;border:none;color:var(--text-muted);font-size:24px;cursor:pointer;padding:0 8px;line-height:1}.modal-close:hover{color:var(--text)}
.modal-body{padding:24px;overflow-y:auto;flex:1}
.hist-date-row{margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:16px}.hist-date-row:last-child{border-bottom:none}
.hist-date{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600;color:var(--accent);margin-bottom:10px}
.hist-table{width:100%;border-collapse:collapse;font-size:13px}
.hist-table th{text-align:left;color:var(--text-muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:6px 8px;border-bottom:1px solid var(--border)}
.hist-table td{padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.03)}
.hist-table .pct-cell{font-family:'JetBrains Mono',monospace;font-weight:600;color:var(--accent);text-align:right;width:80px}
.hist-table .delta-cell{font-family:'JetBrains Mono',monospace;font-size:11px;text-align:right;width:80px}
.delta-up{color:var(--green)}.delta-down{color:var(--amber)}.delta-new{color:var(--green);font-weight:600}.delta-exit{color:var(--red);font-weight:600}
.hist-tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
.hist-tab{background:var(--surface-2);border:1px solid var(--border);color:var(--text-muted);font-size:12px;padding:6px 14px;border-radius:6px;cursor:pointer;font-family:'JetBrains Mono',monospace}.hist-tab:hover,.hist-tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
@media(max-width:768px){.top-bar{padding:16px;flex-direction:column;gap:8px;align-items:flex-start}.container{padding:20px 16px}.company-grid{grid-template-columns:1fr}.search-input{width:100%}.modal{width:95%;max-height:90vh}}
</style></head><body>
<div class="top-bar"><div class="logo"><div class="logo-mark">XI</div><h1><span>XICE</span> Shareholder Tracker</h1></div><div class="meta" id="last-updated">Loading...</div></div>
<div class="container"><div class="stats-row" id="stats-row"></div><div id="changes-section"></div>
<div id="top-movers-section"></div>
<div class="section-title" style="margin-top:32px">All Companies</div>
<div class="controls"><input type="text" class="search-input" id="search" placeholder="Search by company or shareholder name..."><button class="export-btn" onclick="exportToExcel()">&#11123; Export to Excel</button></div>
<div class="company-grid" id="company-grid"></div></div>
<div class="modal-overlay" id="modal-overlay" onclick="if(event.target===this)closeModal()">
<div class="modal"><div class="modal-header"><div><h2 id="modal-title">History</h2></div><div style="display:flex;align-items:center;gap:12px"><span class="ticker-badge" id="modal-ticker"></span><button class="modal-close" onclick="closeModal()">&times;</button></div></div>
<div class="modal-body" id="modal-body"></div></div></div>
<script>
/*__EMBEDDED_DATA__*/
var _allData=null;var _cardData={};
async function loadData(){try{const d=typeof __EMBEDDED_DATA__!=='undefined'?__EMBEDDED_DATA__:await(await fetch('data.json')).json();_allData=d;render(d)}catch(e){document.getElementById('company-grid').innerHTML='<div class="empty-state"><p>No data yet. Run <code>python tracker.py</code></p></div>'}}
function render(d){document.getElementById('last-updated').textContent='Last scan: '+d.date;
const wd=d.companies.filter(c=>c.count>0).length,en=d.changes_today.reduce((s,c)=>s+c.entered.length,0),ex=d.changes_today.reduce((s,c)=>s+c.exited.length,0),sh=d.changes_today.reduce((s,c)=>s+c.changed.length,0),hlen=d.history?d.history.length:0;
document.getElementById('stats-row').innerHTML=`<div class="stat-card"><div class="label">Companies Tracked</div><div class="value">${wd}<span style="font-size:16px;color:var(--text-muted)">/${d.companies.length}</span></div></div><div class="stat-card"><div class="label">New Entries</div><div class="value green">${en}</div></div><div class="stat-card"><div class="label">Exits</div><div class="value red">${ex}</div></div><div class="stat-card"><div class="label">Ownership Shifts</div><div class="value amber">${sh}</div></div><div class="stat-card"><div class="label">History</div><div class="value">${hlen}<span style="font-size:14px;color:var(--text-muted)"> days</span></div></div>`;
const cs=document.getElementById('changes-section');
if(d.changes_today.length>0){let h='<div class="panel"><div class="panel-header"><h2>Today\'s Changes</h2></div><div class="panel-body">';
for(const ch of d.changes_today){for(const e of ch.entered)h+=`<div class="change-item"><span class="badge badge-enter">ENTER</span><div class="change-info"><div class="change-name">${e.name}</div><div class="change-detail">${e.pct}% - rank #${e.rank}</div></div><span class="change-company">${ch.ticker}</span></div>`;
for(const e of ch.exited)h+=`<div class="change-item"><span class="badge badge-exit">EXIT</span><div class="change-info"><div class="change-name">${e.name}</div><div class="change-detail">Was ${e.pct}% - rank #${e.rank}</div></div><span class="change-company">${ch.ticker}</span></div>`;
for(const c of ch.changed){const dr=c.delta>0?'up':'down',a=c.delta>0?'\u25B2':'\u25BC';h+=`<div class="change-item"><span class="badge badge-${dr}">${a} ${Math.abs(c.delta)}pp</span><div class="change-info"><div class="change-name">${c.name}</div><div class="change-detail">${c.old_pct}% \u2192 ${c.new_pct}%</div></div><span class="change-company">${ch.ticker}</span></div>`}}
h+='</div></div>';cs.innerHTML=h}else{cs.innerHTML='<div class="panel"><div class="panel-header"><h2>Today\'s Changes</h2></div><div class="panel-body"><div class="empty-state" style="padding:24px"><p>No changes detected, or first scan.</p></div></div></div>'}
renderTopMovers(d);
renderGrid(d.companies);document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();renderGrid(d.companies.filter(c=>c.ticker.toLowerCase().includes(q)||c.name.toLowerCase().includes(q)||c.shareholders.some(s=>s.name.toLowerCase().includes(q))))})}
function _prevBusinessDay(dates,latestDate){/* Find the most recent date before latestDate that is a weekday (Mon-Fri) */
var candidates=dates.filter(function(d){return d<latestDate});
for(var i=0;i<candidates.length;i++){var dt=new Date(candidates[i]+'T12:00:00Z');var dow=dt.getUTCDay();if(dow>=1&&dow<=5)return candidates[i]}
return candidates.length?candidates[0]:null}
function _computeMovers(latest,prev){
var moves=[];var tickers=Object.keys(latest.companies);
tickers.forEach(function(ticker){var cur=latest.companies[ticker]||[];var old=prev.companies[ticker]||[];
var oldMap={};old.forEach(function(s){oldMap[s.name]=s.pct});
var curMap={};cur.forEach(function(s){curMap[s.name]=s.pct});
cur.forEach(function(s){if(oldMap[s.name]!==undefined){var delta=Math.round((s.pct-oldMap[s.name])*100)/100;if(delta!==0)moves.push({name:s.name,ticker:ticker,pct:s.pct,oldPct:oldMap[s.name],delta:delta,type:delta>0?'up':'down'})}else{moves.push({name:s.name,ticker:ticker,pct:s.pct,oldPct:0,delta:s.pct,type:'enter'})}});
old.forEach(function(s){if(curMap[s.name]===undefined){moves.push({name:s.name,ticker:ticker,pct:0,oldPct:s.pct,delta:-s.pct,type:'exit'})}})});
moves.sort(function(a,b){return Math.abs(b.delta)-Math.abs(a.delta)});
return moves}
function _renderMoverCards(moves,prevDate,latestDate,tm){
var top=moves.slice(0,12);
var h='<div class="top-movers-grid">';
top.forEach(function(m,i){
var arrow='',cls='';
if(m.type==='enter'){arrow='\u25B2 NEW';cls='enter'}
else if(m.type==='exit'){arrow='\u25BC EXIT';cls='exit'}
else if(m.delta>0){arrow='\u25B2 +'+m.delta+'pp';cls='up'}
else{arrow='\u25BC '+m.delta+'pp';cls='down'}
var detail='';
if(m.type==='enter')detail=m.ticker+' \u2014 entered at '+m.pct+'%';
else if(m.type==='exit')detail=m.ticker+' \u2014 exited from '+m.oldPct+'%';
else detail=m.ticker+' \u2014 '+m.oldPct+'% \u2192 '+m.pct+'%';
h+='<div class="mover-card" data-mover-sh="'+m.name.replace(/"/g,'&quot;')+'"><div class="mover-rank" style="color:var(--text-muted)">'+(i+1)+'</div><div class="mover-info"><div class="mover-name">'+m.name+'</div><div class="mover-detail">'+detail+'</div></div><div class="mover-delta '+cls+'">'+arrow+'</div></div>'});
h+='</div>';
document.getElementById('top-movers-body').innerHTML=moves.length?h:'<div class="empty-state" style="padding:24px">No ownership changes between these dates.</div>';
document.getElementById('top-movers-span').textContent=prevDate+' \u2192 '+latestDate}
function renderTopMovers(d){const tm=document.getElementById('top-movers-section');if(!d.history||d.history.length<2){tm.innerHTML='';return}
const latest=d.history[0];
const allDates=d.history.map(function(h){return h.date}).sort(function(a,b){return b.localeCompare(a)});
const otherDates=allDates.filter(function(dt){return dt<latest.date});
if(!otherDates.length){tm.innerHTML='';return}
const defaultPrev=_prevBusinessDay(allDates,latest.date)||otherDates[0];
var opts=otherDates.map(function(dt){return'<option value="'+dt+'"'+(dt===defaultPrev?' selected':'')+'>'+dt+'</option>'}).join('');
let h='<div class="panel" style="margin-top:24px"><div class="panel-header" style="display:flex;align-items:center;justify-content:space-between"><h2>Top Movers <span id="top-movers-span" style="font-size:12px;color:var(--text-muted);font-weight:400;margin-left:8px"></span></h2><div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:var(--text-muted)">Compare from:</span><select id="mover-date-select" style="background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:JetBrains Mono,monospace;font-size:12px;padding:4px 8px;border-radius:6px;cursor:pointer">'+opts+'</select></div></div><div class="panel-body" id="top-movers-body"></div></div>';
tm.innerHTML=h;
var histMap={};d.history.forEach(function(snap){histMap[snap.date]=snap});
function refresh(prevDate){var prev=histMap[prevDate];if(!prev)return;var moves=_computeMovers(latest,prev);_renderMoverCards(moves,prevDate,latest.date,tm)}
refresh(defaultPrev);
document.getElementById('mover-date-select').addEventListener('change',function(){refresh(this.value)});
tm.addEventListener('click',function(e){var card=e.target.closest('.mover-card[data-mover-sh]');if(card)openShareholderProfile(card.dataset.moverSh)})}
function exportToExcel(){if(!_allData)return;
var sep=',';
var rows=[['Ticker','Company','Rank','Shareholder','Ownership %']];
_allData.companies.forEach(function(c){c.shareholders.forEach(function(s){rows.push([c.ticker,'"'+c.name.replace(/"/g,'""')+'"',s.rank,'"'+s.name.replace(/"/g,'""')+'"',s.pct])})});
rows.push([]);rows.push(['--- Changes Today ---']);rows.push(['Ticker','Company','Type','Shareholder','Detail']);
_allData.changes_today.forEach(function(ch){
ch.entered.forEach(function(e){rows.push([ch.ticker,'"'+ch.company.replace(/"/g,'""')+'"','ENTER','"'+e.name.replace(/"/g,'""')+'"',e.pct+'% rank #'+e.rank])});
ch.exited.forEach(function(e){rows.push([ch.ticker,'"'+ch.company.replace(/"/g,'""')+'"','EXIT','"'+e.name.replace(/"/g,'""')+'"','Was '+e.pct+'% rank #'+e.rank])});
ch.changed.forEach(function(c){rows.push([ch.ticker,'"'+ch.company.replace(/"/g,'""')+'"','SHIFT','"'+c.name.replace(/"/g,'""')+'"',c.old_pct+'% -> '+c.new_pct+'% ('+((c.delta>0)?'+':'')+c.delta+'pp)'])})});
if(_allData.history&&_allData.history.length>0){rows.push([]);rows.push(['--- Historical Data ---']);
_allData.history.forEach(function(snap){rows.push([]);rows.push(['Date: '+snap.date]);rows.push(['Ticker','Rank','Shareholder','Ownership %']);
Object.keys(snap.companies).sort().forEach(function(ticker){var shs=snap.companies[ticker];if(shs)shs.forEach(function(s){rows.push([ticker,s.rank,'"'+s.name.replace(/"/g,'""')+'"',s.pct])})})})}
var csv=rows.map(function(r){return r.join(sep)}).join('\n');
var BOM='\uFEFF';
var blob=new Blob([BOM+csv],{type:'text/csv;charset=utf-8;'});
var url=URL.createObjectURL(blob);
var a=document.createElement('a');a.href=url;a.download='xice_shareholders_'+_allData.date+'.csv';a.click();URL.revokeObjectURL(url)}
function renderGrid(cs){const g=document.getElementById('company-grid');if(!cs.length){g.innerHTML='<div class="empty-state"><p>No matches.</p></div>';return}
_cardData={};
g.innerHTML=cs.map((c,i)=>{const id='card-'+i;_cardData[id]={shareholders:c.shareholders,ticker:c.ticker,name:c.name};const hasMore=c.shareholders.length>10;const srcBadge=c.data_source==='morningstar'?'<span class="source-badge" title="Shareholder data from Morningstar">Morningstar</span>':'';return'<div class="company-card"><div class="company-card-header" data-card="'+id+'"><span class="ticker">'+c.ticker+'</span><span class="name" title="'+c.name.replace(/"/g,'&quot;')+'">'+c.name+'</span>'+srcBadge+'</div><div class="company-card-body"><div id="'+id+'-rows">'+( c.shareholders.length?c.shareholders.slice(0,10).map(s=>'<div class="shareholder-row"><span class="rank">#'+s.rank+'</span><span class="sh-name" data-sh="'+s.name.replace(/"/g,'&quot;')+'" title="'+s.name.replace(/"/g,'&quot;')+'">'+s.name+'</span><span class="pct">'+s.pct+'%</span></div>').join(''):'<div class="no-data">No data available</div>')+'</div>'+(hasMore?'<div class="toggle-row" data-toggle="'+id+'" data-expanded="0" style="text-align:center;color:var(--accent);font-size:12px;cursor:pointer;user-select:none;padding:8px 0">&#9660; Show all '+c.shareholders.length+'</div>':'')+'</div></div>'}).join('')}
document.getElementById('company-grid').addEventListener('click',function(e){var shEl=e.target.closest('.sh-name[data-sh]');if(shEl){e.stopPropagation();openShareholderProfile(shEl.dataset.sh);return}var hdr=e.target.closest('.company-card-header');if(hdr&&hdr.dataset.card){var d=_cardData[hdr.dataset.card];if(d)openHistory(d.ticker,d.name);return}var tog=e.target.closest('.toggle-row');if(tog&&tog.dataset.toggle){toggleCard(tog,tog.dataset.toggle)}});
function toggleCard(el,id){var d=_cardData[id];if(!d)return;var sh=d.shareholders;var rows=document.getElementById(id+'-rows');var expanded=el.getAttribute('data-expanded')==='1';if(expanded){rows.innerHTML=sh.slice(0,10).map(s=>'<div class="shareholder-row"><span class="rank">#'+s.rank+'</span><span class="sh-name" data-sh="'+s.name.replace(/"/g,'&quot;')+'" title="'+s.name.replace(/"/g,'&quot;')+'">'+s.name+'</span><span class="pct">'+s.pct+'%</span></div>').join('');el.innerHTML='&#9660; Show all '+sh.length;el.setAttribute('data-expanded','0')}else{rows.innerHTML=sh.map(s=>'<div class="shareholder-row"><span class="rank">#'+s.rank+'</span><span class="sh-name" data-sh="'+s.name.replace(/"/g,'&quot;')+'" title="'+s.name.replace(/"/g,'&quot;')+'">'+s.name+'</span><span class="pct">'+s.pct+'%</span></div>').join('');el.innerHTML='&#9650; Show less';el.setAttribute('data-expanded','1')}}
function openHistory(ticker,name){if(!_allData||!_allData.history||_allData.history.length<1)return;
document.getElementById('modal-title').textContent=name;document.getElementById('modal-ticker').textContent=ticker;
const hist=_allData.history.filter(h=>h.companies&&h.companies[ticker]&&h.companies[ticker].length>0);
if(hist.length<1){document.getElementById('modal-body').innerHTML='<div class="empty-state">No historical data available for this company.</div>';document.getElementById('modal-overlay').classList.add('active');return}
let html='';
for(let i=0;i<hist.length;i++){const snap=hist[i];const sh=snap.companies[ticker];const prevSh=i<hist.length-1?hist[i+1].companies[ticker]:null;
const prevMap={};if(prevSh)prevSh.forEach(s=>{prevMap[s.name]=s.pct});
html+=`<div class="hist-date-row"><div class="hist-date">${snap.date}</div><table class="hist-table"><thead><tr><th>#</th><th>Shareholder</th><th style="text-align:right">Ownership</th>${prevSh?'<th style="text-align:right">Change</th>':''}</tr></thead><tbody>`;
sh.forEach((s,idx)=>{let deltaHtml='';if(prevSh){if(prevMap[s.name]!==undefined){const d=Math.round((s.pct-prevMap[s.name])*100)/100;if(d>0)deltaHtml=`<td class="delta-cell delta-up">\u25B2 +${d}pp</td>`;else if(d<0)deltaHtml=`<td class="delta-cell delta-down">\u25BC ${d}pp</td>`;else deltaHtml='<td class="delta-cell" style="color:var(--text-muted)">\u2014</td>'}else{deltaHtml='<td class="delta-cell delta-new">NEW</td>'}}
html+=`<tr><td style="color:var(--text-muted);font-family:'JetBrains Mono',monospace;font-size:11px">${idx+1}</td><td>${s.name}</td><td class="pct-cell">${s.pct}%</td>${deltaHtml}</tr>`});
if(prevSh){const curNames=new Set(sh.map(s=>s.name));prevSh.forEach(s=>{if(!curNames.has(s.name)){html+=`<tr style="opacity:.6"><td></td><td style="text-decoration:line-through">${s.name}</td><td class="pct-cell">${s.pct}%</td><td class="delta-cell delta-exit">EXIT</td></tr>`}})}
html+='</tbody></table></div>'}
document.getElementById('modal-body').innerHTML=html;document.getElementById('modal-overlay').classList.add('active')}
function openShareholderProfile(name){if(!_allData)return;
document.getElementById('modal-title').textContent=name;document.getElementById('modal-ticker').textContent='SHAREHOLDER';
var html='';
/* Current holdings */
var holdings=[];
_allData.companies.forEach(function(c){c.shareholders.forEach(function(s){if(s.name===name)holdings.push({ticker:c.ticker,company:c.name,pct:s.pct,rank:s.rank,shares:s.shares})})});
if(holdings.length>0){
html+='<div class="sh-profile-section"><h3>Current Holdings<span class="sh-section-badge" style="background:var(--green-bg);color:var(--green)">'+holdings.length+' companies</span></h3>';
holdings.sort(function(a,b){return b.pct-a.pct});
holdings.forEach(function(h){html+='<div class="sh-holding-card"><div class="sh-holding-left"><span class="sh-holding-ticker">'+h.ticker+'</span><span class="sh-holding-name">'+h.company+'</span></div><div class="sh-holding-right"><span class="sh-holding-rank">#'+h.rank+'</span><span class="sh-holding-pct">'+h.pct+'%</span></div></div>'});
html+='</div>'}else{html+='<div class="sh-profile-section"><h3>Current Holdings</h3><div class="empty-state" style="padding:16px">Not currently in any top-20 list.</div></div>'}
/* Historical timeline per company */
if(_allData.history&&_allData.history.length>0){
var companySet={};
_allData.history.forEach(function(snap){Object.keys(snap.companies).forEach(function(ticker){var shs=snap.companies[ticker];if(shs)shs.forEach(function(s){if(s.name===name){if(!companySet[ticker])companySet[ticker]=[];companySet[ticker].push({date:snap.date,pct:s.pct,rank:s.rank})}})})});
/* Also check current holdings for tickers not in history */
holdings.forEach(function(h){if(!companySet[h.ticker])companySet[h.ticker]=[]});
var tickers=Object.keys(companySet).sort();
if(tickers.length>0){
html+='<div class="sh-profile-section"><h3>Historical Positions</h3>';
tickers.forEach(function(ticker){
var entries=companySet[ticker];
var companyName=ticker;
_allData.companies.forEach(function(c){if(c.ticker===ticker)companyName=c.name});
html+='<div class="sh-holding-card" style="flex-direction:column;align-items:stretch"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><div class="sh-holding-left"><span class="sh-holding-ticker">'+ticker+'</span><span class="sh-holding-name">'+companyName+'</span></div></div>';
if(entries.length>0){
html+='<table class="hist-table" style="margin:0"><thead><tr><th>Date</th><th style="text-align:right">Ownership</th><th style="text-align:right">Rank</th><th style="text-align:right">Change</th></tr></thead><tbody>';
entries.sort(function(a,b){return b.date.localeCompare(a.date)});
for(var i=0;i<entries.length;i++){var e=entries[i];var prev=i<entries.length-1?entries[i+1]:null;var deltaHtml='<td class="delta-cell" style="color:var(--text-muted)">\u2014</td>';
if(prev){var d=Math.round((e.pct-prev.pct)*100)/100;if(d>0)deltaHtml='<td class="delta-cell delta-up">\u25B2 +'+d+'pp</td>';else if(d<0)deltaHtml='<td class="delta-cell delta-down">\u25BC '+d+'pp</td>'}else if(entries.length>1){deltaHtml='<td class="delta-cell delta-new">NEW</td>'}
html+='<tr><td style="font-family:JetBrains Mono,monospace;font-size:12px;color:var(--accent)">'+e.date+'</td><td class="pct-cell">'+e.pct+'%</td><td style="text-align:right;color:var(--text-muted);font-family:JetBrains Mono,monospace;font-size:11px">#'+e.rank+'</td>'+deltaHtml+'</tr>'}
html+='</tbody></table>'}else{html+='<div style="font-size:12px;color:var(--text-muted);font-style:italic">Current position only (no prior history)</div>'}
html+='</div>'});
html+='</div>'}}
document.getElementById('modal-body').innerHTML=html;document.getElementById('modal-overlay').classList.add('active')}
function closeModal(){document.getElementById('modal-overlay').classList.remove('active')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal()});
loadData();
</script></body></html>"""


def run_debug_html(ticker: str):
    """Scrape a single company using the full pipeline and save raw HTML for inspection."""
    ticker = ticker.upper()
    company = next((c for c in XICE_COMPANIES if c["ticker"] == ticker), None)
    if not company:
        log.error(f"Unknown ticker: {ticker}. Available: {', '.join(c['ticker'] for c in XICE_COMPANIES)}")
        return

    url = company["shareholder_url"]
    if not url:
        log.error(f"{ticker} has no URL configured.")
        return

    log.info(f"Debug: fetching {ticker} ({company['name']}) from {url}")

    # Save raw HTML for inspection
    html = None
    if not company.get("needs_js", False):
        html = fetch_page(url)
    if not html:
        log.info(f"Debug: using Selenium for {ticker}...")
        html = fetch_with_selenium(url, wait_seconds=10)

    if html:
        debug_path = BASE_DIR / f"debug_{ticker}.html"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Debug: saved raw HTML ({len(html):,} chars) to {debug_path}")

    # Run the full scrape pipeline (includes specialized scrapers)
    shareholders = scrape_company(company)
    close_driver()

    if shareholders:
        log.info(f"Debug: found {len(shareholders)} shareholders:")
        for s in shareholders:
            log.info(f"  #{s['rank']:>2}  {s['name']:<45} {s['pct']:>6}%  ({s['shares']:>12,} shares)")
    else:
        log.warning(f"Debug: no shareholders found for {ticker}.")


def main():
    parser = argparse.ArgumentParser(description="XICE Shareholder Tracker")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--time", default="08:30")
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--test-email", action="store_true")
    parser.add_argument("--debug-html", metavar="TICKER", help="Scrape one company and save raw HTML for debugging")
    args = parser.parse_args()
    if args.debug_html:
        run_debug_html(args.debug_html)
        return
    if args.test_email:
        c = load_config()
        t = [{"ticker":"TEST","company":"Test hf.","entered":[{"name":"New Fund","pct":5.2,"rank":8}],"exited":[{"name":"Old Co.","pct":3.1,"rank":15}],"changed":[{"name":"Big Investor","old_pct":12.0,"new_pct":14.5,"delta":2.5}],"has_changes":True}]
        send_email(f"XICE Test ({today_str()})", build_email_html(t, today_str()), c)
        return
    if args.dashboard:
        s = load_snapshot(today_str())
        if not s:
            log.error("No snapshot for today.")
            return
        # Recompute diffs so changes are preserved
        config = load_config()
        threshold = config.get("change_threshold_pct", 0.5)
        prev = get_previous_snapshot()
        all_changes = []
        if prev:
            pc = prev.get("companies", {})
            for c in XICE_COMPANIES:
                t = c["ticker"]
                if pc.get(t) and s["companies"].get(t):
                    all_changes.append(diff_shareholders(pc[t], s["companies"][t], t, c["name"], threshold))
        generate_dashboard(all_changes, s["companies"], today_str())
        return
    if args.schedule: run_scheduler(args.time)
    else: run_scan()

if __name__ == "__main__":
    main()
