import unittest
from unittest.mock import patch

from app.agent.qa import AnswerOutputError, Citation, QATrace, generate_answer
from app.services.deepseek_client import DeepSeekError, ModelJSONError


def source() -> Citation:
    return Citation(
        evidence_id="E1", chunk_id="chunk-1", document_id="DOC-1",
        version_id="DOC-1-v1", title="测试制度", version="1.0",
        sections=[], clause_numbers=[], source_format="md", locations=[],
    )


class AnswerFormatRecoveryTest(unittest.TestCase):
    @patch("app.agent.qa.complete_json")
    def test_retries_schema_error_with_original_evidence(self, complete):
        payload = {"question": "问题", "evidence": [{"id": "E1", "text": "证据"}]}
        complete.side_effect = [
            {"data": {"status": "answer", "answer": "结论[E1]", "citation_ids": "E1"}},
            {"data": {"status": "answer", "answer": "结论[E1]", "citation_ids": ["E1"]}},
        ]
        trace = QATrace()

        answer, citations = generate_answer("规则", payload, {"E1": source()}, trace)

        self.assertEqual(answer.status, "answer")
        self.assertEqual([citation.chunk_id for citation in citations], ["chunk-1"])
        self.assertEqual(complete.call_count, 2)
        first, second = (call.kwargs for call in complete.call_args_list)
        self.assertIs(first["user_payload"], payload)
        self.assertIs(second["user_payload"], payload)
        self.assertEqual(first["system_prompt"], "规则")
        self.assertIn("list_type", second["system_prompt"])
        self.assertEqual(trace.answer_attempts, 2)
        self.assertEqual(len(trace.answer_attempt_seconds), 2)
        self.assertEqual(trace.answer_format_errors, [[{"field": "citation_ids", "type": "list_type"}]])

    @patch("app.agent.qa.complete_json")
    def test_rejects_invalid_citation_after_single_correction(self, complete):
        complete.side_effect = [
            ModelJSONError("invalid output", "invalid_json"),
            {"data": {"status": "answer", "answer": "结论[E9]", "citation_ids": ["E9"]}},
        ]
        trace = QATrace()

        with self.assertRaises(AnswerOutputError):
            generate_answer("规则", {}, {"E1": source()}, trace)

        self.assertEqual(complete.call_count, 2)
        self.assertEqual(
            trace.answer_format_errors,
            [
                [{"field": "<root>", "type": "invalid_json"}],
                [{"field": "citation_ids", "type": "invalid_or_mismatched_references"}],
            ],
        )

    @patch("app.agent.qa.complete_json", side_effect=DeepSeekError("DeepSeek网络请求失败"))
    def test_transport_error_does_not_trigger_format_correction(self, complete):
        trace = QATrace()
        with self.assertRaises(DeepSeekError):
            generate_answer("规则", {}, {"E1": source()}, trace)
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(trace.answer_format_errors, [])


if __name__ == "__main__":
    unittest.main()
