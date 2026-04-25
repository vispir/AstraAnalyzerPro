from playwright.sync_api import sync_playwright
import time

def set_timezone_utc4():
    """Open Forex Factory and set timezone to UTC+4"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context()
        page = context.new_page()

        print("Opening Forex Factory...")
        page.goto("https://www.forexfactory.com/", wait_until="networkidle")

        time.sleep(2)

        print("Looking for timezone selector...")

        # Try to find and click timezone/settings
        # Forex Factory usually has timezone in the calendar page
        page.goto("https://www.forexfactory.com/calendar", wait_until="networkidle")

        time.sleep(2)

        # Look for timezone dropdown or settings
        try:
            # Click on timezone selector if visible
            timezone_selector = page.locator("select[name='timezone'], .timezone-select, #timezone")
            if timezone_selector.count() > 0:
                print("Found timezone selector, setting to UTC+4...")
                timezone_selector.select_option(label="UTC+4")
                print("Timezone set to UTC+4!")
            else:
                print("Timezone selector not found. Looking for settings...")
                # Try to find settings link
                settings = page.get_by_text("Settings", exact=False)
                if settings.count() > 0:
                    settings.first.click()
                    time.sleep(2)
        except Exception as e:
            print(f"Error: {e}")
            print("You may need to set timezone manually.")

        print("Browser will stay open for 5 minutes...")
        time.sleep(300)

        browser.close()

if __name__ == "__main__":
    set_timezone_utc4()
