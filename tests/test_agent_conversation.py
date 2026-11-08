import unittest
from datetime import date
from unittest.mock import Mock, patch
from uuid import uuid4

from app.agent.conversation import parse_condition, respond_to_turn
from app.agent.qa import QAResponse
from app.agent.session import ClarificationStore, SessionBusyError
from app.services.deepseek_client import DeepSeekError


QUERY_DATE = date(2026, 10, 17)


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.store = ClarificationStore()
        self.key = ("U001", uuid4())
        self.resolve_query = Mock(return_value=(QUERY_DATE, "PUR-001"))
        self.answer = Mock(side_effect=self.respond)

    def respond(self, question, query_date, document_id, trace):
        if question == "这笔采购需要几家报价？":
            trace.clarification_rule = "procurement_total"
            return QAResponse(status="clarify", answer="请补充累计采购金额。", as_of_date=query_date, citations=[])
        return QAResponse(status="answer", answer="已有结果。", as_of_date=query_date, citations=[])

    def turn(self, text, key=None, new_question=False):
        return respond_to_turn(
            self.store, key or self.key, text, new_question, self.resolve_query, self.answer,
        )

    def test_valid_reply_uses_original_date_and_document_and_clears_state(self):
        self.assertTrue(self.turn("这笔采购需要几家报价？")[1])
        response, awaiting = self.turn("累计金额是 6,000 元")
        self.assertEqual(response.status, "answer")
        self.assertFalse(awaiting)
        question, query_date, document_id, _ = self.answer.call_args.args
        self.assertIn("同一需求的累计采购金额为6,000元", question)
        self.assertEqual((query_date, document_id), (QUERY_DATE, "PUR-001"))
        self.resolve_query.assert_called_once()
        self.assertFalse(self.turn("那 8000 元呢？")[1])
        self.assertEqual(self.answer.call_count, 2)

    def test_two_unclear_replies_close_the_pending_turn(self):
        self.turn("这笔采购需要几家报价？")
        self.assertTrue(self.turn("6000元可以吗？")[1])
        result, awaiting = self.turn("不知道")
        self.assertFalse(awaiting)
        self.assertIn("原问题", result.answer)
        self.assertFalse(self.turn("6000元")[1])
        self.answer.assert_called_once()

    def test_clear_new_topic_uses_its_own_query_metadata(self):
        self.turn("这笔采购需要几家报价？")
        self.resolve_query.return_value = (date(2026, 11, 1), None)
        self.assertFalse(self.turn("6000 元的差旅能报吗？")[1])
        query, query_date, document_id, _ = self.answer.call_args.args
        self.assertEqual((query, query_date, document_id), ("6000 元的差旅能报吗？", date(2026, 11, 1), None))

    def test_service_error_does_not_consume_a_valid_reply(self):
        self.turn("这笔采购需要几家报价？")
        self.answer.side_effect = DeepSeekError("暂不可用")
        with self.assertRaises(DeepSeekError):
            self.turn("六千元")
        self.answer.side_effect = self.respond
        self.assertEqual(self.turn("六千元")[0].status, "answer")
        self.assertIn("六千元", self.answer.call_args.args[0])

    def test_expiry_is_measured_from_first_question(self):
        with patch("app.agent.conversation.monotonic", return_value=100), patch(
            "app.agent.session.monotonic", return_value=100
        ) as clock:
            self.turn("这笔采购需要几家报价？")
            clock.return_value = 999
            self.assertTrue(self.turn("差不多")[1])
            clock.return_value = 1000
            self.assertFalse(self.turn("6000元")[1])
        self.answer.assert_called_once()

    def test_isolation_by_user_and_session_and_busy_turn(self):
        self.turn("这笔采购需要几家报价？")
        self.assertFalse(self.turn("6000元", ("U002", self.key[1]))[1])
        self.assertFalse(self.turn("6000元", ("U001", uuid4()))[1])
        with self.store.turn(self.key):
            with self.assertRaises(SessionBusyError):
                self.turn("6000元")
        self.assertEqual(self.turn("6000元")[0].status, "answer")

    def test_workdays_require_the_expected_unit(self):
        self.assertEqual(parse_condition("4 个工作日", "leave_workdays"), "4个工作日")
        self.assertEqual(parse_condition("四", "leave_workdays"), "四个工作日")
        self.assertIsNone(parse_condition("4 个自然日", "leave_workdays"))
        self.assertIsNone(parse_condition("0 元", "payment_total"))
        self.assertIsNone(parse_condition("大约 6000 元", "payment_total"))
