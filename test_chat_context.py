"""Check what the chat actually sends to the model.

These assemble messages rather than calling Ollama, so they run in a moment
and say the same thing either way: the conversation that arrives has the turn
the next question refers to, carries the document when there is one, and fits
the window it was given.

The bug these were written for: the page sliced the last exchange off the
history before sending it, so "rewrite that, more casual" arrived with the
thing to rewrite missing, and the model answered that it had no text to work
from -- having written that text itself a moment earlier.

    .\\.venv\\Scripts\\python.exe test_chat_context.py
"""
from __future__ import annotations

import unittest

import vision_server as V

BUDGET = (V.CHAT_CTX - V.ANSWER_RESERVE_TOKENS) * V.CHARS_PER_TOKEN


def size(messages):
    return sum(len(m["content"]) for m in messages)


def joined(messages):
    return "\n".join(m["content"] for m in messages)


def exchange(n, length=20):
    return [{"role": "user", "content": f"question {n} " + "x" * length},
            {"role": "assistant", "content": f"answer {n} " + "y" * length}]


class TheTurnBeingReferredTo(unittest.TestCase):
    def test_the_most_recent_answer_is_sent(self):
        """The whole bug: "rewrite that" needs the thing it means."""
        history = exchange(1) + exchange(2)
        messages = V._build_messages("rewrite it, more casual", history, "", "")
        self.assertIn("answer 2", joined(messages))

    def test_every_turn_of_a_short_conversation_survives(self):
        history = exchange(1) + exchange(2) + exchange(3)
        messages = V._build_messages("and now?", history, "", "")
        self.assertEqual(len(messages), 1 + len(history) + 1)
        self.assertEqual([m["role"] for m in messages][:3],
                         ["system", "user", "assistant"])

    def test_the_question_is_last(self):
        messages = V._build_messages("the question", exchange(1), "", "")
        self.assertEqual(messages[-1], {"role": "user",
                                        "content": "the question"})

    def test_blank_and_malformed_turns_are_dropped(self):
        history = [{"role": "user", "content": "   "},
                   {"role": "nobody", "content": "ignore me"},
                   {"role": "assistant", "content": "kept"}]
        messages = V._build_messages("q", history, "", "")
        self.assertEqual([m["role"] for m in messages],
                         ["system", "assistant", "user"])


class TheDocument(unittest.TestCase):
    def test_a_transcript_is_sent_with_the_question(self):
        messages = V._build_messages("what is the account number?", [],
                                     "Account Number EL-88342710", "bill.pdf")
        self.assertIn("Transcript of bill.pdf", messages[1]["content"])
        self.assertIn("EL-88342710", messages[1]["content"])

    def test_the_instruction_changes_when_there_is_one(self):
        with_doc = V._build_messages("q", [], "some text", "a.pdf")[0]["content"]
        without = V._build_messages("q", [], "", "")[0]["content"]
        self.assertIn("Use only the transcript", with_doc)
        self.assertIn("cannot see any document", without)

    def test_a_finished_read_is_analysed_with_what_was_found(self):
        """The analysis gets the analyst prompt and the label search's hits;
        a value that was not found is not offered as one."""
        req = V.ChatReq(prompt="", mode="analyse", document="Total 41.32",
                        document_name="r.pdf", fields=[
                            {"field": "total", "value": "41.32", "verdict": "yes", "page": 1},
                            {"field": "taxes", "value": "", "verdict": "none", "page": 1}])
        question, messages = V._chat_request(req)
        self.assertEqual(question, V.ANALYSE_DEFAULT)
        self.assertIn("Checks", messages[0]["content"])
        self.assertIn("- total: 41.32 [page 1]", messages[0]["content"])
        self.assertNotIn("taxes", messages[0]["content"])
        # A follow-up question is answered, not analysed from scratch.
        _, follow = V._chat_request(V.ChatReq(prompt="what is the total?", document="Total 41.32"))
        self.assertNotIn("Key figures", follow[0]["content"])

    def test_a_long_document_sends_the_pages_the_question_needs(self):
        """Cutting to the first and last pages is how "check page 74" got
        "I don't have page 74". The page asked about, and the pages holding
        the question's words, are what go; the model is told the rest exist."""
        pages = [f"<!-- ===== page {n} ===== -->\nfiller text {n} " + "z" * 3000
                 for n in range(1, 200)]
        pages[73] = "<!-- ===== page 74 ===== -->\nComparable Sales Adjustment Grid " + "z" * 3000
        pages[140] = "<!-- ===== page 141 ===== -->\nThe zoning is M-1 light industrial " + "z" * 3000
        doc = "\n".join(pages)
        sent = V._build_messages("check page 74", [], doc, "big.pdf")[1]["content"]
        self.assertIn("Comparable Sales Adjustment Grid", sent)
        self.assertIn("199 pages", sent)
        self.assertIn("left out to fit", sent)
        sent = V._build_messages("what is the zoning?", [], doc, "big.pdf")[1]["content"]
        self.assertIn("M-1 light industrial", sent)
        self.assertLessEqual(len(sent), BUDGET)

    def test_a_follow_up_keeps_the_topic_of_the_question_before(self):
        pages = [f"<!-- ===== page {n} ===== -->\nfiller {n} " + "z" * 3000 for n in range(1, 200)]
        pages[88] = "<!-- ===== page 89 ===== -->\nThe capitalization rate concluded is 6.75% " + "z" * 3000
        req = V.ChatReq(prompt="and on what page?", document="\n".join(pages),
                        history=[{"role": "user", "content": "what capitalization rate was concluded?"},
                                 {"role": "assistant", "content": "6.75%"}])
        _, messages = V._chat_request(req)
        self.assertIn("concluded is 6.75%", joined(messages))

    def test_one_huge_page_is_still_sent_in_part(self):
        """A document with no page markers bigger than the window is cut into
        parts, not dropped."""
        doc = "HEAD " + "z" * 400_000 + " TAIL"
        sent = V._build_messages("summarise", [], doc, "big.docx")[1]["content"]
        self.assertIn("HEAD", sent)
        self.assertLessEqual(len(sent), BUDGET)


class TheWindow(unittest.TestCase):
    """Going over it does not fail; Ollama drops from the front instead."""

    def test_a_long_conversation_is_cut_to_fit(self):
        history = []
        for i in range(60):
            history += exchange(i, 900)
        messages = V._build_messages("and now?", history, "", "")
        self.assertLessEqual(size(messages), BUDGET)

    def test_what_survives_is_the_recent_end(self):
        history = []
        for i in range(160):
            history += exchange(i, 900)
        text = joined(V._build_messages("and now?", history, "", ""))
        self.assertIn("answer 159", text)
        self.assertNotIn("question 0 ", text)

    def test_the_system_prompt_is_never_crowded_out(self):
        history = []
        for i in range(60):
            history += exchange(i, 900)
        messages = V._build_messages("q", history, "x" * 100_000, "big.pdf")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Never invent a figure", messages[0]["content"])
        self.assertLessEqual(size(messages), BUDGET)

    def test_a_document_and_a_conversation_share_the_window(self):
        history = []
        for i in range(40):
            history += exchange(i, 900)
        messages = V._build_messages("q", history, "d" * 100_000, "big.pdf")
        self.assertLessEqual(size(messages), BUDGET)
        self.assertGreater(len(messages), 3, "the history was dropped entirely")

    def test_order_is_oldest_to_newest(self):
        history = exchange(1) + exchange(2) + exchange(3)
        sent = [m["content"] for m in
                V._build_messages("q", history, "", "")[1:-1]]
        self.assertLess(sent.index([s for s in sent if "answer 1" in s][0]),
                        sent.index([s for s in sent if "answer 3" in s][0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
