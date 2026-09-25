from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from frontend.api_client import APIError
from frontend.preview import sample_data

APP = str(Path(__file__).resolve().parents[1] / "frontend" / "app.py")


class AppTests(unittest.TestCase):
    def setUp(self):
        self.patches = []
        for name, value in [("get_health", "health"), ("get_nodes", "nodes"), ("get_repair_status", "repairs"),
                            ("get_metrics", "metrics"), ("get_files", "files"), ("get_events", "events")]:
            mock = patch("frontend.api_client.APIClient." + name, return_value=sample_data()[value])
            mock.start()
            self.patches.append(mock)

    def tearDown(self):
        for mock in self.patches:
            mock.stop()

    def test_all_pages_render(self):
        app = AppTest.from_file(APP, default_timeout=20).run()
        self.assertFalse(app.exception)
        for page in ["Files", "Recovery", "Connections", "Overview"]:
            app.radio[0].set_value(page).run()
            self.assertFalse(app.exception, page)

    def test_preview_is_labeled_and_actions_disabled(self):
        app = AppTest.from_file(APP, default_timeout=20).run()
        app.radio[1].set_value("Design preview").run()
        self.assertTrue(any("sample data" in x.value for x in app.info))
        app.radio[0].set_value("Files").run()
        upload = next(b for b in app.button if b.label == "Upload to Vault")
        self.assertTrue(upload.disabled)

    def test_service_down_shows_no_fake_health(self):
        with patch("frontend.api_client.APIClient.get_health", side_effect=APIError("Unavailable")), \
             patch("frontend.api_client.APIClient.get_repair_status", side_effect=APIError("Unavailable")):
            app = AppTest.from_file(APP, default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Waiting for verified system status" in x.value for x in app.markdown))

    def test_search_and_delete_require_confirmation(self):
        with patch("frontend.api_client.APIClient.delete_file") as delete:
            app = AppTest.from_file(APP, default_timeout=20).run()
            app.radio[0].set_value("Files").run()
            next(b for b in app.button if b.label == "Delete selected object").click().run()
            delete.assert_not_called()
            next(c for c in app.checkbox if c.label == "I confirm deletion of this object").check().run()
            next(b for b in app.button if b.label == "Delete selected object").click().run()
            delete.assert_called_once_with("design-brief")
            self.assertFalse(app.exception)

    def test_invalid_connection_address_is_explained(self):
        app = AppTest.from_file(APP, default_timeout=20).run()
        app.radio[0].set_value("Connections").run()
        next(t for t in app.text_input if t.label == "Storage API address").set_value("not-a-url")
        next(b for b in app.button if b.label == "Save connections").click().run()
        self.assertTrue(app.error)
        self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
