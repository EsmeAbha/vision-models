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

BUDGET = (8192 - V.ANSWER_RESERVE_TOKENS) * V.CHARS_PER_TOKEN


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
        self.assertIn("Answer from it", with_doc)
        self.assertIn("cannot see any document", without)

    def test_a_long_document_keeps_both_ends_and_says_so(self):
        """A silent cut in the middle reads as a document that stops."""
        doc = "HEAD " + "z" * 200_000 + " TAIL"
        messages = V._build_messages("summarise", [], doc, "big.pdf")
        sent = messages[1]["content"]
        self.assertIn("HEAD", sent)
        self.assertIn("TAIL", sent)
        self.assertIn("not shown", sent)
        self.assertLessEqual(size(messages), BUDGET)


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
        for i in range(60):
            history += exchange(i, 900)
        text = joined(V._build_messages("and now?", history, "", ""))
        self.assertIn("answer 59", text)
        self.assertNotIn("question 0 ", text)

    def test_the_system_prompt_is_never_crowded_out(self):
        history = []
        for i in range(60):
            history += exchange(i, 900)
        messages = V._build_messages("q", history, "x" * 100_000, "big.pdf")
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("careful assistant", messages[0]["content"])
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
