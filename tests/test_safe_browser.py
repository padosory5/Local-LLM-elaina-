import unittest

from tools.browser_control.safe_browser import SafeBrowserControl


class ResolveSearchTests(unittest.TestCase):
    def test_builds_a_url_from_the_default_template(self):
        browser = SafeBrowserControl()

        resolution = browser.resolve_search("best hotels in Guam")

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(
            resolution.url,
            "https://www.google.com/search?q=best+hotels+in+Guam",
        )

    def test_percent_encodes_special_characters_in_the_query(self):
        browser = SafeBrowserControl()

        resolution = browser.resolve_search("C++ tutorials & tips")

        self.assertEqual(resolution.status, "resolved")
        self.assertNotIn("&", resolution.url.split("q=", 1)[1])
        self.assertIn("C%2B%2B", resolution.url)

    def test_refuses_an_empty_query(self):
        browser = SafeBrowserControl()

        resolution = browser.resolve_search("   ")

        self.assertEqual(resolution.status, "invalid_target")

    def test_honors_a_configured_search_engine_template(self):
        browser = SafeBrowserControl(
            search_url_template="https://duckduckgo.com/?q={query}",
        )

        resolution = browser.resolve_search("wireless keyboards")

        self.assertEqual(
            resolution.url, "https://duckduckgo.com/?q=wireless+keyboards",
        )


if __name__ == "__main__":
    unittest.main()
