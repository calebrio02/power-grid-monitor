import unittest
from html.parser import HTMLParser
from pathlib import Path

class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.bars = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section':
            self.sections.append(attrs.get('aria-label'))
        if attrs.get('id', '').startswith('bar-b'):
            self.bars.append(attrs)

class DashboardDesignTest(unittest.TestCase):
    def test_empty_comparison_has_no_fabricated_split(self):
        html = (Path(__file__).parents[1] / 'server/static/index.html').read_text()
        self.assertIn('id="split-lab-bar" style="width: 0%;"', html)
        self.assertIn('id="split-house-bar" style="width: 0%;"', html)

    def test_monitoring_view_precedes_history_and_has_no_fabricated_bars(self):
        page = DashboardParser()
        page.feed((Path(__file__).parents[1] / 'server/static/index.html').read_text())
        self.assertLess(page.sections.index('Nodos'), page.sections.index('Historial de potencia'))
        self.assertTrue(page.bars)
        self.assertTrue(all('width: 0%' in bar.get('style', '') for bar in page.bars))

if __name__ == '__main__':
    unittest.main()
