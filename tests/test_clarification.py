import unittest

from app.agent.clarification import clarify_missing_condition


EVIDENCE = [
    {"id": "E1", "text": "金额阈值按同一合同或同一事项累计应付金额判断。"},
    {"id": "E2", "text": "报价数量按同一需求累计金额判断，不得通过拆单规避比价要求。"},
    {"id": "E3", "text": "连续请假不超过3个工作日由部门负责人审批；超过3个工作日由部门负责人审核后报HR负责人审批。"},
    {"id": "E4", "text": "连续请假不超过5个工作日由部门负责人审批；超过5个工作日由部门负责人审核后报HR负责人审批。"},
]
VERSIONS = {"E1": "payment-v1", "E2": "procurement-v2", "E3": "leave-v2", "E4": "general-v1"}


class ClarificationTest(unittest.TestCase):
    def test_payment_requests_cumulative_payable_amount(self):
        result = clarify_missing_condition("本次对公付款该由谁审批？", EVIDENCE, VERSIONS)
        self.assertEqual(result.rule, "payment_total")
        self.assertIn("同一合同或同一事项计算的累计应付金额", result.answer)
        self.assertEqual(result.citation_ids, ["E1"])

    def test_procurement_requests_same_need_total(self):
        result = clarify_missing_condition("我准备采购一批设备，要找几家供应商报价？", EVIDENCE, VERSIONS)
        self.assertIn("同一需求计算的累计采购金额", result.answer)
        self.assertEqual(result.citation_ids, ["E2"])

    def test_leave_conflict_uses_intersection_of_actual_thresholds(self):
        result = clarify_missing_condition("我打算请事假，谁审批？", EVIDENCE, VERSIONS)
        self.assertTrue(result.answer.startswith("请补充这次连续请假的工作日数。"))
        self.assertIn("4至5个工作日", result.answer)
        self.assertEqual(result.citation_ids, ["E3", "E4"])

    def test_present_conditions_and_unanswerable_subquestions_are_left_to_v1(self):
        for question in (
            "这笔付款两万元由谁审批？",
            "本次采购6000元需要几家报价？",
            "本次2万的采购需要几家报价？",
            "我打算请4个工作日的事假，谁审批？",
            "出差结束后多久报销？日本出差的日元住宿上限是多少？",
        ):
            with self.subTest(question=question):
                self.assertIsNone(clarify_missing_condition(question, EVIDENCE, VERSIONS))

    def test_matching_question_without_supporting_evidence_is_left_to_v1(self):
        self.assertIsNone(clarify_missing_condition("这笔对公付款由谁审批？", [], {}))
