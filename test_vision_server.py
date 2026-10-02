"""Checks for the chat front end's server, with the models mocked out.

Nothing here touches the GPU: ensure_model and _run_one are patched, so these
run on any machine and in a few seconds. What they cover is the glue that can
break silently -- the shape of what /api/models and /api/skills hand the page,
and the PDF path, where render_pdf yields (page, path, info) tuples rather
than the list of paths it looks like it returns.

One side effect worth knowing: importing vision_server imports app.py, which
registers stop_vllm with atexit. So finishing a test run stops the WSL vLLM
server -- including one a running Gradio page was using. Restart that page's
model, or just run these when nothing else is mid-read.
"""
import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import vision_server as server

# A valid 1x1 RGBA PNG, so uploads can be tested without pulling in Pillow.
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
       b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def wait_for(client, rid, timeout=10):
    """Runs happen on a worker thread; poll until one settles."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/api/runs/{rid}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError(f"run {rid} never finished")


class VisionServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def upload(self, name="doc.png", data=PNG):
        r = self.client.post("/api/upload", files={"file": (name, data)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ------------------------------------------------------------ what's on

    def test_models_come_from_app_py(self):
        models = self.client.get("/api/models").json()
        self.assertTrue(models, "app.py offered no models")
        ids = [m["id"] for m in models]
        self.assertEqual(ids, list(server.vision.MODELS.values()))
        for m in models:
            self.assertTrue(m["tabs"], f"{m['id']} declares no tabs")
            self.assertIn("takes_prompt", m)
        # PaddleOCR-VL runs a fixed pipeline, so the page must not offer a box.
        paddle = next(m for m in models if m["id"] == "paddleocr_vl")
        self.assertFalse(paddle["takes_prompt"])

    def test_skills_reference_real_models(self):
        skills = self.client.get("/api/skills").json()
        known = set(server.vision.MODELS.values())
        for k in skills:
            self.assertIn(k["model"], known, f"{k['name']} names no real model")
            self.assertTrue(k["name"])
            self.assertTrue(k["model_short"])

    # -------------------------------------------------------------- uploads

    def test_upload_rejects_other_file_types(self):
        r = self.client.post("/api/upload", files={"file": ("notes.txt", b"hi")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("not an image or a PDF", r.json()["detail"])

    def test_upload_then_preview(self):
        up = self.upload()
        self.assertFalse(up["pdf"])
        self.assertEqual(up["preview"], f"/api/files/{up['id']}/preview")
        r = self.client.get(up["preview"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PNG)

    def test_preview_of_an_unknown_file_is_404(self):
        self.assertEqual(self.client.get("/api/files/nope/preview").status_code, 404)

    # ------------------------------------------------------------------ runs

    def test_a_run_reports_every_step_and_its_text(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one",
                          return_value=("# Heading\n\nbody", None)) as one:
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr", "prompt": "read it",
            }).json()
            state = wait_for(self.client, started["id"])

        self.assertEqual(state["status"], "done")
        self.assertEqual(state["text"], "# Heading\n\nbody")
        self.assertIsNone(state["annotated"])
        self.assertEqual([s["state"] for s in state["steps"]], ["done"] * 3)
        # The prompt the page sent is the prompt the model got.
        self.assertEqual(one.call_args.args[2], "read it")

    def test_a_fixed_pipeline_model_gets_no_prompt(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one",
                          return_value=("<table></table>", None)) as one:
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "paddleocr_vl", "prompt": "ignored",
            }).json()
            wait_for(self.client, started["id"])
        # The page blanks it, but assert the server does not invent one either.
        self.assertEqual(one.call_args.args[2], "ignored")

    def test_a_failing_run_reports_the_error(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one",
                          side_effect=RuntimeError("worker died")):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            state = wait_for(self.client, started["id"])

        self.assertEqual(state["status"], "error")
        self.assertIn("worker died", state["error"])

    def test_run_rejects_an_unknown_model_and_a_stale_file(self):
        up = self.upload()
        r = self.client.post("/api/run", json={"file_id": up["id"], "model": "gpt"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/run", json={"file_id": "gone", "model": "deepseek_ocr"})
        self.assertEqual(r.status_code, 404)

    def test_events_replay_a_finished_run(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one", return_value=("text", None)):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            wait_for(self.client, started["id"])

        body = self.client.get(f"/api/runs/{started['id']}/events").text
        events = [json.loads(line[len("data: "):])
                  for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(len([e for e in events if e["type"] == "step"]), 3)
        self.assertEqual(events[-1]["type"], "done")

    # ------------------------------------------------------------------ PDFs

    def test_pdf_pages_are_consumed_as_tuples(self):
        """render_pdf yields (page number, png path, info) -- not paths."""
        rendered = [(2, "/tmp/page_002.png", {}), (3, "/tmp/page_003.png", {})]
        with patch.object(server.pdf_pages, "page_count", return_value=9), \
             patch.object(server.pdf_pages, "render_pdf", return_value=iter(rendered)) as rp, \
             patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_ocr_request_batch",
                          return_value=["two", "three"]) as batch:
            up = self.upload("scan.pdf", b"%PDF-1.4 fake")
            self.assertTrue(up["pdf"])
            self.assertEqual(up["pages"], 9)

            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "paddleocr_vl", "pages": "2-3",
            }).json()
            state = wait_for(self.client, started["id"])

        self.assertEqual(state["status"], "done", state["error"])
        # The page spec goes through untouched -- parsing it here as well would
        # hand render_pdf a list where it expects '2-3'.
        self.assertEqual(rp.call_args.kwargs["pages"], "2-3")
        # Only the paths are passed on, and the real page numbers label them.
        self.assertEqual(batch.call_args.args[0],
                         ["/tmp/page_002.png", "/tmp/page_003.png"])
        self.assertIn("<!-- page 2 -->\ntwo", state["text"])
        self.assertIn("<!-- page 3 -->\nthree", state["text"])

    def test_an_unreadable_pdf_is_refused_at_upload(self):
        with patch.object(server.pdf_pages, "page_count",
                          side_effect=ValueError("not a PDF")):
            r = self.client.post("/api/upload",
                                 files={"file": ("broken.pdf", b"nope")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("unreadable PDF", r.json()["detail"])

    # ------------------------------------------------- doc types and fields

    def test_doc_types_name_real_readers_and_fields(self):
        types = self.client.get("/api/doc_types").json()
        self.assertTrue(types, "no document types on disk")
        known = set(server.vision.MODELS.values())
        names = [t["name"] for t in types]
        for wanted in ("Appraisal", "Bank statement", "Utility bill",
                       "Rent roll", "Lease document", "Tax bill"):
            self.assertIn(wanted, names, f"{wanted} is missing")
        for t in types:
            self.assertIn(t["reader"], known,
                          f"{t['name']} names no real reader")
            self.assertIsInstance(t["fields"], list)
            self.assertTrue(t["id"])
            # Rent roll, Lease and Tax bill carry no field list on purpose:
            # the whole document is the answer, or formats vary too much to
            # fix a list. A type with no fields has to say why instead.
            if not t["fields"]:
                self.assertTrue(t["why"],
                                f"{t['name']} has neither fields nor a reason")

        by_name = {t["name"]: t for t in types}
        self.assertEqual(len(by_name["Appraisal"]["fields"]), 8)
        self.assertEqual(by_name["Rent roll"]["fields"], [])

    def finished_run(self, text):
        """A completed run whose transcript is `text`."""
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one", return_value=(text, None)):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            wait_for(self.client, started["id"])
        return started["id"]

    def test_fields_are_found_in_the_transcript(self):
        rid = self.finished_run(
            "ELECTRIC BILL\n"
            "Account Number  EL-88342710\n"
            "Bill Date  09/05/2026\n"
            "Total Due  $142.67\n")
        r = self.client.post(f"/api/runs/{rid}/fields", json={
            "fields": ["Account number", "Bill date", "Cap rate"],
        })
        self.assertEqual(r.status_code, 200, r.text)
        rows = {x["field"]: x for x in r.json()["rows"]}
        self.assertEqual(rows["Account number"]["value"], "EL-88342710")
        self.assertEqual(rows["Account number"]["verdict"], "yes")
        self.assertEqual(rows["Bill date"]["value"], "09/05/2026")
        # A field the page does not carry is reported missing, never invented.
        self.assertEqual(rows["Cap rate"]["value"], "")
        self.assertEqual(rows["Cap rate"]["verdict"], "none")
        self.assertIn("2 of 3 found", r.json()["summary"])
        # Every row carries where the value sat, so a wrong answer is visible.
        self.assertTrue(rows["Account number"]["evidence"])

    def test_extra_typed_fields_are_searched_too(self):
        rid = self.finished_run("Meter Number  Z-55\nTariff  Economy 7\n")
        r = self.client.post(f"/api/runs/{rid}/fields", json={
            "fields": [], "extra": "Meter number, Tariff",
        }).json()
        self.assertEqual([x["field"] for x in r["rows"]],
                         ["Meter number", "Tariff"])
        self.assertEqual(r["rows"][0]["value"], "Z-55")

    def test_fields_are_kept_on_the_run(self):
        rid = self.finished_run("Account Number  EL-1\n")
        self.client.post(f"/api/runs/{rid}/fields",
                         json={"fields": ["Account number"]})
        state = self.client.get(f"/api/runs/{rid}").json()
        self.assertEqual(len(state["fields"]), 1)
        self.assertIn("1 of 1 found", state["field_summary"])

    def test_fields_refuses_an_empty_request(self):
        rid = self.finished_run("Account Number  EL-1\n")
        r = self.client.post(f"/api/runs/{rid}/fields",
                             json={"fields": [], "extra": "  "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("at least one field", r.json()["detail"])

    def test_fields_needs_a_run_with_text(self):
        self.assertEqual(
            self.client.post("/api/runs/nope/fields",
                             json={"fields": ["X"]}).status_code, 404)
        rid = self.finished_run("   ")
        r = self.client.post(f"/api/runs/{rid}/fields",
                             json={"fields": ["X"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no text", r.json()["detail"])

    def test_no_model_is_called_to_extract_fields(self):
        """Extraction is a text search; app.py's compose_prompt returns ''."""
        rid = self.finished_run("Account Number  EL-9\n")
        with patch.object(server.vision, "_run_one") as one, \
             patch.object(server.vision, "ensure_model") as ens:
            self.client.post(f"/api/runs/{rid}/fields",
                             json={"fields": ["Account number"]})
        one.assert_not_called()
        ens.assert_not_called()

    # ------------------------------------------------------------------ save

    def test_save_refuses_an_empty_result(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one", return_value=("   ", None)):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            wait_for(self.client, started["id"])
        r = self.client.post(f"/api/runs/{started['id']}/save")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/api/runs/x/download/0").status_code, 404)

    def test_save_writes_the_files_it_lists(self):
        up = self.upload()
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one", return_value=("# hi", None)):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            wait_for(self.client, started["id"])

        with patch.object(server.save_output, "save_all",
                          return_value=["/tmp/a.md", "/tmp/a.xlsx"]):
            saved = self.client.post(f"/api/runs/{started['id']}/save").json()
        self.assertEqual([f["name"] for f in saved["saved"]], ["a.md", "a.xlsx"])

    # ------------------------------------------------------------- the page

    def test_the_page_and_its_assets_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for path in ("/assets/style.css", "/assets/app.js"):
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
