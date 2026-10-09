"""Checks for the chat front end's server, with the models mocked out.

Nothing here touches the GPU: ensure_model and _run_one are patched, so these
run on any machine and in a few seconds. What they cover is the glue that can
break silently -- the shape of what /api/models and /api/skills hand the page,
and the PDF path, where render_pdf yields (page, path, info) tuples rather
than the list of paths it looks like it returns.

Importing vision_server imports app.py. Its exit hook only stops vLLM if
that same process started it, so a test run no longer stops the shared
vLLM server a running page is using.
"""
import json
import os
import shutil
import tempfile
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
        # Runs are remembered on disk, and these tests do dozens of them.
        # Without this they pile fixtures called doc.png and scan.pdf into
        # the history a person actually reads.
        self.history = tempfile.mkdtemp(prefix="hist_")
        self.addCleanup(shutil.rmtree, self.history, ignore_errors=True)
        patcher = patch.object(server.run_history, "HISTORY_DIR", self.history)
        patcher.start()
        self.addCleanup(patcher.stop)

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
             patch.object(server.vision, "ocr_batch_with_layout",
                          return_value=(["two", "three"], [None, None])) as batch:
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

    def test_a_finished_run_is_remembered(self):
        body = chr(10).join(['# a heading', '', 'body'])
        rid = self.finished_run(body)
        remembered = self.client.get('/api/history').json()
        self.assertEqual([h['id'] for h in remembered], [rid])
        self.assertEqual(remembered[0]['file'], 'doc.png')
        self.assertEqual(remembered[0]['model_name'], 'DeepSeek-OCR')

        full = self.client.get(f'/api/history/{rid}').json()
        self.assertEqual(full['text'], body)

    def test_a_failed_run_is_not_remembered(self):
        """Nothing was read, so there is nothing to come back to."""
        up = self.upload()
        with patch.object(server.vision, "ensure_model"),              patch.object(server.vision, "_run_one",
                          side_effect=RuntimeError("worker died")):
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "deepseek_ocr",
            }).json()
            wait_for(self.client, started["id"])
        self.assertEqual(self.client.get("/api/history").json(), [])

    def test_pulling_fields_does_not_reorder_the_history(self):
        """Refreshing a record must not float it back to the top."""
        first = self.finished_run('Account Number  A-1')
        second = self.finished_run('Account Number  B-2')
        self.client.post(f"/api/runs/{first}/fields",
                         json={"fields": ["Account number"]})
        order = [h["id"] for h in self.client.get("/api/history").json()]
        self.assertEqual(order[0], second, "an older run jumped to the top")

    def test_a_remembered_run_can_be_forgotten(self):
        rid = self.finished_run("text")
        self.assertEqual(
            self.client.delete(f"/api/history/{rid}").status_code, 200)
        self.assertEqual(self.client.get("/api/history").json(), [])
        self.assertEqual(
            self.client.delete(f"/api/history/{rid}").status_code, 404)

    # ------------------------------------------------------- not leaking

    def test_old_uploads_are_evicted_and_their_folders_removed(self):
        """Each upload keeps a temp folder; 642 of them had piled up."""
        import os as _os
        kept = []
        for i in range(server.MAX_UPLOADS + 5):
            kept.append(self.upload(f"doc_{i}.png"))
        with server._state_lock:
            live = len(server._uploads)
        self.assertLessEqual(live, server.MAX_UPLOADS,
                             "uploads grew past the cap")
        # The evicted ones took their folders with them.
        gone = [u for u in kept[:5]
                if not _os.path.isdir(_os.path.dirname(
                    server._uploads.get(u["id"], {}).get("path", "x")))]
        self.assertTrue(gone, "an evicted upload left its folder behind")

    def test_an_upload_a_run_is_reading_is_not_evicted(self):
        """Age is no reason to delete the file a run is in the middle of."""
        up = self.upload("busy.png")
        with server._state_lock:
            server._runs["busyrun"] = {"status": "running",
                                       "upload": up["id"]}
        try:
            for i in range(server.MAX_UPLOADS + 5):
                self.upload(f"filler_{i}.png")
            with server._state_lock:
                self.assertIn(up["id"], server._uploads,
                              "evicted an upload a run was still reading")
        finally:
            with server._state_lock:
                server._runs.pop("busyrun", None)

    def test_finished_runs_are_evicted_from_memory(self):
        """Each run holds its whole transcript; history keeps them on disk."""
        with server._state_lock:
            before = len(server._runs)
            for i in range(server.MAX_RUNS + 10):
                server._runs[f"old{i}"] = {"status": "done", "text": "x" * 100}
            server._evict_runs()
            after = len(server._runs)
        self.assertLessEqual(after, server.MAX_RUNS + 1,
                             f"runs grew unbounded: {before} -> {after}")

    def test_sweeping_leaves_fresh_folders_alone(self):
        import os as _os
        import tempfile as _tf
        fresh = _tf.mkdtemp(prefix="vision_up_")
        self.addCleanup(shutil.rmtree, fresh, ignore_errors=True)
        server.sweep_temp(hours=6)
        self.assertTrue(_os.path.isdir(fresh),
                        "the sweep deleted a folder still in use")
        # Backdated past the cutoff, it goes.
        _os.utime(fresh, (time.time() - 99999, time.time() - 99999))
        server.sweep_temp(hours=6)
        self.assertFalse(_os.path.isdir(fresh), "a stale folder survived")

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


class PickFromPCTests(unittest.TestCase):
    """The Windows file dialog path: the file comes in with where it lives.

    The dialog itself is not opened -- subprocess.run is stubbed to answer as
    the dialog would -- so these never put a window on anyone's screen.
    """

    def setUp(self):
        self.client = TestClient(server.app)
        self.history = tempfile.mkdtemp(prefix="hist_")
        self.addCleanup(shutil.rmtree, self.history, ignore_errors=True)
        patcher = patch.object(server.run_history, "HISTORY_DIR", self.history)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A real document in a real folder, as the dialog would hand it back.
        self.folder = tempfile.mkdtemp(prefix="APR_")
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.doc = os.path.join(self.folder, "Courtyard bill.png")
        with open(self.doc, "wb") as fh:
            fh.write(PNG)

    def answer(self, path, code=0):
        import subprocess
        return patch("subprocess.run", return_value=subprocess.CompletedProcess(
            [], code, stdout=json.dumps(path) + "\n", stderr=""))

    def test_a_picked_file_keeps_its_full_path(self):
        with self.answer(self.doc):
            r = self.client.post("/api/pick")
        self.assertEqual(r.status_code, 200, r.text)
        up = r.json()
        self.assertEqual(up["source_path"], os.path.normpath(self.doc))
        self.assertEqual(up["name"], "Courtyard bill.png")

        # ... and the run made from it remembers that path after a restart.
        with patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "_run_one", return_value=("text", None)):
            rid = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "paddleocr_vl"}).json()["id"]
            self.assertEqual(wait_for(self.client, rid)["status"], "done")
        server._runs.pop(rid, None)
        again = self.client.get(f"/api/history/{rid}").json()
        self.assertEqual(again["source_path"], os.path.normpath(self.doc))
        self.assertEqual(server._runs[rid]["source_path"], os.path.normpath(self.doc))

    def test_cancelling_the_dialog_attaches_nothing(self):
        with self.answer(""):
            r = self.client.post("/api/pick")
        self.assertEqual(r.json(), {"cancelled": True})

    def test_the_dialog_script_is_valid_python(self):
        compile(server._PICK_SCRIPT, "<pick>", "exec")

    def test_only_documents_are_taken(self):
        other = os.path.join(self.folder, "notes.txt")
        open(other, "w").close()
        with self.answer(other):
            r = self.client.post("/api/pick")
        self.assertEqual(r.status_code, 400)


class ProgressTests(unittest.TestCase):
    """A long read says how far it has got, page by page, as it goes."""

    def setUp(self):
        self.client = TestClient(server.app)
        self.history = tempfile.mkdtemp(prefix="hist_")
        self.addCleanup(shutil.rmtree, self.history, ignore_errors=True)
        patcher = patch.object(server.run_history, "HISTORY_DIR", self.history)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_progress_is_recorded_while_reading(self):
        rendered = [(1, "/tmp/p1.png", {}), (2, "/tmp/p2.png", {}), (3, "/tmp/p3.png", {})]

        def read(images, progress=None):
            for done in (1, 2, 3):
                progress(done, 3)
            return ["a", "b", "c"], [None] * 3

        events = []
        with patch.object(server.pdf_pages, "page_count", return_value=3), \
             patch.object(server.pdf_pages, "render_pdf", return_value=iter(rendered)), \
             patch.object(server.vision, "ensure_model"), \
             patch.object(server.vision, "ocr_batch_with_layout", side_effect=read):
            up = self.client.post("/api/upload",
                                  files={"file": ("bill.pdf", b"%PDF-1.4")}).json()
            started = self.client.post("/api/run", json={
                "file_id": up["id"], "model": "paddleocr_vl"}).json()
            wait_for(self.client, started["id"])
            run = server._runs[started["id"]]
            # Drain what was queued for a live listener, in order.
            while not run["events"].empty():
                events.append(run["events"].get_nowait())
        notes = [e["note"] for e in events if e.get("type") == "step" and e.get("i") == 1]
        self.assertEqual(notes[:4], ["page 0 of 3", "page 1 of 3", "page 2 of 3", "page 3 of 3"])
        totals = [(e["done"], e["total"]) for e in events
                  if e.get("type") == "step" and e.get("i") == 1 and e["state"] == "active"]
        self.assertEqual(totals, [(0, 3), (1, 3), (2, 3), (3, 3)])
        active = [e for e in events if e.get("type") == "step" and e["state"] == "active"]
        self.assertTrue(all(e["since"] for e in active), "an active step has no start time")


class ZipBatchTests(unittest.TestCase):
    """A zip in the chat is every document inside it, each held to be read."""

    def setUp(self):
        self.client = TestClient(server.app)

    def zip_of(self, members):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def upload_zip(self, data, name="Appraisal.zip"):
        return self.client.post("/api/upload", files={"file": (name, data)})

    def forget(self, files):
        for f in files:
            e = server._uploads.pop(f["id"], None)
            if e:
                shutil.rmtree(e["folder"], ignore_errors=True)

    def test_each_document_is_held_with_a_path_through_the_zip(self):
        r = self.upload_zip(self.zip_of({
            "set/a.png": PNG, "set/b.png": PNG,
            "__MACOSX/set/._a.png": b"junk", "set/~$lock.png": PNG,
            "set/notes.txt": b"not a document",
        }))
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.addCleanup(self.forget, d["files"])
        self.assertTrue(d["batch"])
        self.assertEqual([f["inner"] for f in d["files"]], ["set/a.png", "set/b.png"])
        self.assertEqual(d["files"][0]["source_path"], "Appraisal.zip/set/a.png")

    def test_a_zip_with_no_document_is_refused(self):
        r = self.upload_zip(self.zip_of({"readme.txt": b"hello"}))
        self.assertEqual(r.status_code, 400)
        self.assertIn("no PDF or image", r.json()["detail"])

    def test_a_large_batch_is_not_evicted_before_it_is_read(self):
        n = server.MAX_UPLOADS + 5
        r = self.upload_zip(self.zip_of({f"doc{i:02d}.png": PNG for i in range(n)}))
        d = r.json()
        self.addCleanup(self.forget, d["files"])
        self.assertEqual(len(d["files"]), n)
        self.assertTrue(all(f["id"] in server._uploads for f in d["files"]),
                        "the upload cap evicted documents still waiting to be read")

    def test_a_picked_zip_keeps_the_full_path_to_each_document(self):
        import subprocess
        folder = tempfile.mkdtemp(prefix="zip_")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        zpath = os.path.join(folder, "Appraisal.zip")
        with open(zpath, "wb") as fh:
            fh.write(self.zip_of({"sub/a.png": PNG}))
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(zpath) + "\n", stderr="")
        with patch("subprocess.run", return_value=done):
            d = self.client.post("/api/pick").json()
        self.addCleanup(self.forget, d["files"])
        self.assertEqual(d["files"][0]["source_path"],
                         os.path.join(os.path.normpath(zpath), "sub", "a.png"))


class PieceUploadTests(unittest.TestCase):
    """Large files arrive in pieces, as they must through the tunnel."""

    def setUp(self):
        self.client = TestClient(server.app)

    def send(self, name, data, piece=7, order=None, skip=None, repeat=None):
        pid = self.client.post("/api/upload/start",
                               json={"name": name, "size": len(data)}).json()["id"]
        offsets = list(range(0, len(data), piece))
        for at in (order(offsets) if order else offsets):
            if at == skip:
                continue
            for _ in range(2 if at == repeat else 1):
                r = self.client.put(f"/api/upload/{pid}/at/{at}",
                                    content=data[at:at + piece])
                self.assertEqual(r.status_code, 200, r.text)
        return self.client.post(f"/api/upload/{pid}/finish")

    def tearDown(self):
        for fid in list(server._uploads):
            e = server._uploads.pop(fid)
            shutil.rmtree(e["folder"], ignore_errors=True)

    def test_pieces_in_any_order_and_repeated_make_the_file(self):
        r = self.send("doc.png", PNG, order=lambda o: list(reversed(o)), repeat=7)
        self.assertEqual(r.status_code, 200, r.text)
        with open(server._uploads[r.json()["id"]]["path"], "rb") as fh:
            self.assertEqual(fh.read(), PNG)

    def test_a_missing_piece_is_refused_not_read(self):
        r = self.send("doc.png", PNG, skip=14)
        self.assertEqual(r.status_code, 400)
        self.assertIn("did not arrive whole", r.json()["detail"])

    def test_a_zip_in_pieces_becomes_a_batch(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.png", PNG)
            zf.writestr("b.png", PNG)
        r = self.send("Appraisal.zip", buf.getvalue(), piece=64)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["files"]), 2)

    def test_a_piece_past_the_end_is_refused(self):
        pid = self.client.post("/api/upload/start",
                               json={"name": "doc.png", "size": 10}).json()["id"]
        r = self.client.put(f"/api/upload/{pid}/at/8", content=b"12345")
        self.assertEqual(r.status_code, 400)


class ChunkedReadTests(unittest.TestCase):
    """A long document is read a few pages per worker call, never all at once."""

    def test_pages_go_to_the_reader_in_chunks_and_come_back_in_order(self):
        n = 20
        pages = [(i, f"/tmp/p{i}.png", {}) for i in range(1, n + 1)]
        calls, seen = [], []

        def read(images, progress=None):
            calls.append(len(images))
            for done in range(1, len(images) + 1):
                progress(done, len(images))
            return [f"text of {p}" for p in images], [None] * len(images)

        with patch.object(server.vision, "ocr_batch_with_layout", side_effect=read):
            text, _, _ = server._read_pages(
                pages, type("R", (), {"prompt": ""})(), "paddleocr_vl",
                lambda done, total: seen.append((done, total)))
        self.assertEqual(calls, [8, 8, 4], "pages were not read in chunks of eight")
        self.assertEqual(seen[-1], (20, 20))
        self.assertEqual([d for d, _ in seen], list(range(1, 21)),
                         "page progress did not count straight through the chunks")
        self.assertLess(text.index("p9.png"), text.index("p17.png"))
