# pip install selenium icalendar pytz python-dateutil
# Then: python3 ymca_university_hills_scrape_firefox.py

import re, time, sys
from datetime import datetime, timedelta
import pytz
from dateutil import parser as dateparser
from icalendar import Calendar, Event

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FFOptions
from selenium.webdriver.firefox.service import Service as FFService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = "https://denverymca.org/university-hills-y-fitness-schedule"
TIMEZONE = "America/Denver"
OUTPUT_ICS = "ymca_university_hills.ics"

def make_driver(headless=True):
    opts = FFOptions()
    if headless:
        opts.add_argument("-headless")
    # Let Selenium 4+ manage geckodriver automatically (no webdriver-manager)
    return webdriver.Firefox(options=opts)

def accept_cookies_if_any(driver):
    selectors = [
        "#onetrust-accept-btn-handler",
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='accept' i]",
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            driver.execute_script("arguments[0].click();", el)
            time.sleep(0.4)
            return
        except Exception:
            pass
    # Fallback: look for any button with “accept”
    try:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            t = (b.text or "").strip().lower()
            if "accept" in t:
                driver.execute_script("arguments[0].click();", b)
                time.sleep(0.4)
                return
    except Exception:
        pass

def parse_range_to_datetimes(range_text, date_str, tz):
    m = re.search(r"(\d{1,2}:\d{2}\s*[ap]m)\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)", range_text, re.I)
    if not m:
        return None, None
    base_date = datetime.strptime(date_str, "%Y-%m-%d")
    start = tz.localize(dateparser.parse(m.group(1), default=base_date))
    end = tz.localize(dateparser.parse(m.group(2), default=base_date))
    if end <= start:
        end += timedelta(hours=12)
    return start, end

def scrape_day(driver, date_iso, tz):
    out = []
    rows = driver.find_elements(By.CSS_SELECTOR, ".fkl-location--timetable .timetable-row")
    for row in rows:
        try:
            time_txt = row.find_element(By.CSS_SELECTOR, ".timetable-row--time").text.strip()
        except Exception:
            time_txt = ""
        try:
            title = row.find_element(By.CSS_SELECTOR, ".timetable-row--title").text.strip()
        except Exception:
            title = ""
        try:
            trainer = row.find_element(By.CSS_SELECTOR, ".timetable-row--trainer").text.strip()
        except Exception:
            trainer = ""
        try:
            location = row.find_element(By.CSS_SELECTOR, ".timetable-row--location").text.strip()
        except Exception:
            location = "University Hills-Schlessman YMCA"

        if not time_txt or not title:
            continue

        start, end = parse_range_to_datetimes(time_txt, date_iso, tz)
        if not start:
            m = re.search(r"(\d{1,2}:\d{2}\s*[ap]m)", time_txt, re.I)
            if m:
                base_date = datetime.strptime(date_iso, "%Y-%m-%d")
                start = tz.localize(dateparser.parse(m.group(1), default=base_date))
                end = start + timedelta(minutes=60)
        if not start:
            continue

        out.append({
            "title": title,
            "start": start,
            "end": end,
            "trainer": trainer,
            "location": location
        })
    return out

def main():
    tz = pytz.timezone(TIMEZONE)
    cal = Calendar()
    cal.add('prodid', '-//YMCA University Hills (scraped)//mxm//')
    cal.add('version', '2.0')

    driver = make_driver(headless=True)  # set False to watch it
    driver.get(URL)

    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    accept_cookies_if_any(driver)

    # Wait for the fisikal widget root
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#fisikal-widget"))
    )
    # Give their jQuery a moment to populate the widget
    time.sleep(2)

    # Ensure the date slider exists
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".fkl-location--date #fkl-date-slider"))
    )
    slider = driver.find_element(By.CSS_SELECTOR, ".fkl-location--date #fkl-date-slider")
    date_tabs = slider.find_elements(By.CSS_SELECTOR, ".date-filter")

    total = 0
    for idx in range(len(date_tabs)):
        # Tabs can be re-rendered; re-query each loop
        slider = driver.find_element(By.CSS_SELECTOR, ".fkl-location--date #fkl-date-slider")
        tabs = slider.find_elements(By.CSS_SELECTOR, ".date-filter")
        tab = tabs[idx]
        date_iso = tab.get_attribute("data-date")
        if not date_iso:
            continue

        # Click via JS (more reliable than .click() on some themes)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tab)
        driver.execute_script("arguments[0].click();", tab)

        # Wait until current date attribute matches our target
        try:
            WebDriverWait(driver, 15).until(
                EC.text_to_be_present_in_element_attribute(
                    (By.CSS_SELECTOR, ".fkl-location--current-date"),
                    "data-curdate",
                    date_iso
                )
            )
        except Exception:
            time.sleep(1)

        # Ensure timetable container exists for this day
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".fkl-location--timetable"))
        )
        time.sleep(0.4)

        # Scrape rows
        day_events = scrape_day(driver, date_iso, tz)

        # De-dupe and add to ICS
        seen = set()
        for ev in day_events:
            key = (ev["title"], ev["start"], ev["end"])
            if key in seen:
                continue
            seen.add(key)

            ical_ev = Event()
            ical_ev.add('summary', ev["title"])
            ical_ev.add('dtstart', ev["start"])
            ical_ev.add('dtend', ev["end"])
            desc = []
            if ev["trainer"]:
                desc.append(f"Instructor: {ev['trainer']}")
            if ev["location"]:
                desc.append(f"Room/Studio: {ev['location']}")
            desc.append("Source: University Hills Y schedule")
            ical_ev.add('description', "\n".join(desc))
            ical_ev.add('location', "University Hills-Schlessman YMCA, Denver, CO")
            cal.add_component(ical_ev)
            total += 1

    driver.quit()

    with open(OUTPUT_ICS, "wb") as f:
        f.write(cal.to_ical())
    print(f"✅ Wrote {OUTPUT_ICS} with {total} class events.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Fatal error:", e)
        sys.exit(1)
