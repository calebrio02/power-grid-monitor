import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='power-monitor-test-', dir=os.environ.get('TMPDIR'))
spec = importlib.util.spec_from_file_location('power_server', Path(__file__).parents[1] / 'server/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class StatusTest(unittest.TestCase):
    def test_old_poll_does_not_claim_current_total(self):
        server.get_setting = lambda *args: '100'
        server.live_state['b650'] = {'online': True, 'wall_watts': 120.0}
        server.live_state['b450'] = {'online': True, 'wall_watts': 90.0}
        server.live_state['last_poll'] = time.time() - 30
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.DashboardHTTPHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/status') as response:
                data = json.load(response)
            self.assertIsNone(data['total_wall_watts'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_offline_node_never_contributes_to_complete_total_or_cost(self):
        server.get_setting = lambda *args: '100'
        server.live_state['b650'] = {'online': True, 'wall_watts': 120.0, 'last_seen': 100}
        server.live_state['b450'] = {'online': False, 'wall_watts': 90.0, 'last_seen': 90}
        server.live_state['last_poll'] = time.time()
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.DashboardHTTPHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/status') as response:
                data = json.load(response)
            self.assertIsNone(data['total_wall_watts'])
            self.assertIsNone(data['cost_per_hour_colones'])
            self.assertIsNone(data['cost_per_day_proj_colones'])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

if __name__ == '__main__':
    unittest.main()
