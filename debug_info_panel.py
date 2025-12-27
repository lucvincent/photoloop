#!/usr/bin/env python3
# Copyright (c) 2025 Luc Vincent. All Rights Reserved.
"""Debug script to inspect Google Photos info panel DOM structure."""

import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

# Album URL to test - use album 2 which has photos with descriptions
ALBUM_URL = "https://photos.app.goo.gl/XPAsoYbWCy2yR5Ct6"  # Rebecca & Galen in Jackson

def main():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')

    service = Service('/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)

    try:
        print(f"Loading album: {ALBUM_URL}")
        driver.get(ALBUM_URL)
        time.sleep(5)

        # Click first photo
        photos = driver.find_elements(By.CSS_SELECTOR, "[data-latest-bg]")
        if photos:
            print(f"Found {len(photos)} photos, clicking first one...")
            photos[0].click()
            time.sleep(2)

            # Open info panel with 'i' key
            print("Opening info panel...")
            ActionChains(driver).send_keys('i').perform()
            time.sleep(2)

            # Find the info panel - usually a sidebar or overlay
            # Try various common patterns
            selectors_to_try = [
                "[role='complementary']",  # Sidebar
                "[aria-label*='info' i]",
                "[aria-label*='details' i]",
                ".info-panel",
                "[class*='info']",
                "[class*='sidebar']",
                "[class*='detail']",
            ]

            print("\n=== Looking for info panel container ===")
            for selector in selectors_to_try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"\nSelector '{selector}' found {len(elements)} elements")
                    for i, el in enumerate(elements[:2]):
                        print(f"  Element {i}: tag={el.tag_name}, class={el.get_attribute('class')[:80] if el.get_attribute('class') else 'none'}")

            # Dump the page HTML to find structure
            print("\n=== Page HTML structure (looking for description/location) ===")

            # Look for elements that might contain description
            desc_candidates = driver.find_elements(By.CSS_SELECTOR,
                "[aria-label*='description' i], [aria-label*='caption' i], [placeholder*='description' i], "
                "[aria-label*='Add a description' i], [aria-label*='Edit description' i], "
                "textarea, [contenteditable='true']")
            print(f"\nDescription candidates: {len(desc_candidates)}")
            for el in desc_candidates[:10]:
                text = el.text[:100] if el.text else el.get_attribute('value') or el.get_attribute('placeholder') or ''
                aria = el.get_attribute('aria-label') or ''
                print(f"  - {el.tag_name}: aria-label={aria[:50]}, text={text[:50]}")

            # Look for elements that might contain location
            loc_candidates = driver.find_elements(By.CSS_SELECTOR,
                "[aria-label*='location' i], [aria-label*='map' i], [href*='maps.google']")
            print(f"\nLocation candidates: {len(loc_candidates)}")
            for el in loc_candidates[:5]:
                text = el.text[:100] if el.text else ''
                print(f"  - {el.tag_name}: aria-label={el.get_attribute('aria-label')}, href={el.get_attribute('href')}, text={text[:50]}")

            # Save full HTML for manual inspection
            html_file = "/tmp/google_photos_info_panel.html"
            with open(html_file, 'w') as f:
                f.write(driver.page_source)
            print(f"\nFull HTML saved to: {html_file}")

            # Also print the visible text structure
            print("\n=== Visible text in body ===")
            body_text = driver.find_element(By.TAG_NAME, 'body').text
            lines = [l.strip() for l in body_text.split('\n') if l.strip()]
            for i, line in enumerate(lines[:30]):
                print(f"  {i}: {line[:80]}")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
