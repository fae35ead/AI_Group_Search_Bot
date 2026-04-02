from playwright.sync_api import sync_playwright


def main() -> None:
  with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page()
    page.goto('data:text/html,<title>playwright-ok</title><h1>ready</h1>')
    print(page.title())
    browser.close()


if __name__ == '__main__':
  main()
