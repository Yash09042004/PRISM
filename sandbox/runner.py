#!/usr/bin/env python3
import sys
import json
import time
import os
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException

def sanitize_filename(url):
    """Create a safe filename from a URL"""
    parsed = urlparse(url)
    path = parsed.path.strip('/').replace('/', '_')
    if not path:
        path = 'homepage'
    # Limit length
    path = path[:50]
    # Remove characters unsafe in filenames
    safe = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in path)
    return safe or 'page'

def discover_links(driver, base_url):
    """Discover internal links on the page"""
    discovered = []
    try:
        links = driver.find_elements(By.TAG_NAME, 'a')
        base_parsed = urlparse(base_url)
        base_domain = base_parsed.netloc

        for link in links:
            try:
                href = link.get_attribute('href')
                if not href:
                    continue
                # Resolve relative URLs
                href = urljoin(base_url, href)
                parsed = urlparse(href)
                # Only http(s) internal links
                if parsed.scheme not in ('http', 'https'):
                    continue
                if parsed.netloc != base_domain:
                    continue
                # Strip fragment
                clean = parsed._replace(fragment='').geturl()
                if clean not in discovered:
                    discovered.append(clean)
            except Exception:
                continue
    except Exception as e:
        print(f"Link discovery error: {e}")

    return discovered

def main():
    if len(sys.argv) < 2:
        print("Usage: runner.py <URL>")
        sys.exit(1)

    url = sys.argv[1].strip()

    # Auto-fix bare URLs like 'youtube.com' → 'https://youtube.com'
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url

    max_pages = 20  # Maximum pages to screenshot
    output_dir = "/out"
    hard_timeout = 150  # seconds — well under Docker's 180s limit

    print(f"Starting multi-page analysis of {url}...")

    # Set up Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--allow-running-insecure-content")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Enable browser logging (needed for get_log to work)
    chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    # Detect chromium binary location (Debian vs Ubuntu)
    for candidate in ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]:
        if os.path.exists(candidate):
            chrome_options.binary_location = candidate
            print(f"Using browser: {candidate}")
            break

    # Detect chromedriver location
    chromedriver_path = "/usr/bin/chromedriver"
    for candidate in ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver",
                      "/usr/lib/chromium-browser/chromedriver"]:
        if os.path.exists(candidate):
            chromedriver_path = candidate
            break
    print(f"Using chromedriver: {chromedriver_path}")

    # Initialize driver
    service = Service(chromedriver_path)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(25)

    all_logs = []
    screenshot_count = 0
    screenshot_manifest = []

    to_visit = [url]
    visited = set()
    start_time = time.time()

    try:
        while to_visit and screenshot_count < max_pages and (time.time() - start_time) < hard_timeout:
            current_url = to_visit.pop(0)
            if current_url in visited:
                continue

            visited.add(current_url)

            try:
                print(f"[{screenshot_count + 1}/{max_pages}] Visiting {current_url}...")
                driver.get(current_url)
                # Wait for page load — longer for first page
                time.sleep(3 if screenshot_count == 0 else 2)

                # Take screenshot
                page_name = sanitize_filename(current_url)
                screenshot_name = f"screenshot_{screenshot_count + 1}_{page_name}.png"
                driver.save_screenshot(os.path.join(output_dir, screenshot_name))

                screenshot_count += 1
                screenshot_manifest.append({
                    "number": screenshot_count,
                    "url": current_url,
                    "filename": screenshot_name,
                    "type": "homepage" if screenshot_count == 1 else "internal_page"
                })
                print(f"  ✓ Screenshot saved: {screenshot_name}")

                # Collect browser console logs (wrapped separately — may not work on all Chromium builds)
                try:
                    logs = driver.get_log("browser")
                    for log in logs:
                        all_logs.append({
                            "page": current_url,
                            "level": log["level"],
                            "message": log["message"],
                            "source": log.get("source", "unknown"),
                            "timestamp": log["timestamp"]
                        })
                except Exception as log_err:
                    print(f"  ℹ Log collection skipped: {log_err}")

                # Discover new internal links
                if screenshot_count < max_pages:
                    new_links = discover_links(driver, url)  # use original base URL
                    added = 0
                    for link in new_links:
                        if link not in visited and link not in to_visit:
                            to_visit.append(link)
                            added += 1
                    if added > 0:
                        print(f"  Discovered {added} new internal links to explore")

            except TimeoutException:
                print(f"  ✗ Page load timed out: {current_url}")
            except WebDriverException as e:
                print(f"  ✗ WebDriver error: {str(e)[:120]}")
            except Exception as e:
                print(f"  ✗ Error visiting {current_url}: {str(e)[:120]}")

        # Save all logs to JSON
        with open(os.path.join(output_dir, "console.json"), "w") as f:
            json.dump(all_logs, f, indent=2)
        print(f"\n✓ Logs saved ({len(all_logs)} entries)")

        # Save screenshot manifest
        with open(os.path.join(output_dir, "screenshots.json"), "w") as f:
            json.dump({
                "total_screenshots": screenshot_count,
                "base_url": url,
                "screenshots": screenshot_manifest
            }, f, indent=2)
        print(f"✓ Screenshot manifest saved")

        print(f"\n✅ Analysis complete: {screenshot_count} screenshots captured")

    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
