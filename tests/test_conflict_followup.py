import unittest
from unittest.mock import Mock

from app.agent.conflict_followup import COMPARISON, Policy, find_missing_relations, relation_pairs


POLICIES = {
    "SPECIAL-v2": Policy("SPECIAL", "员工差旅管理制度"),
    "GENERAL-v1": Policy("GENERAL", "员工日常业务执行标准"),
}


class ConflictFollowupTest(unittest.TestCase):
    def test_own_version_replacement_is_not_a_cross_policy_relation(self):
        text = "本制度自2026-09-15起施行，原V1.0自生效日起废止。"
        self.assertEqual(relation_pairs("SPECIAL-v2", text, POLICIES), set())

    def test_topic_specific_supplement_identifies_both_policies(self):
        text = "公司既有差旅、请假和账号专项制度继续保留，本文件不作废止、替代或优先适用的决定。"
        self.assertEqual(
            relation_pairs("GENERAL-v1", text, POLICIES),
            {frozenset(("SPECIAL-v2", "GENERAL-v1"))},
        )
        db = Mock()
        self.assertEqual(
            find_missing_relations(db, POLICIES, [("GENERAL-v1", text)], set()),
            ("no_missing_relation", []),
        )
        db.scalars.assert_not_called()

    def test_comparison_gate_leaves_open_ended_approval_question_alone(self):
        self.assertIsNone(COMPARISON.search("我准备请事假，需要谁审批？"))
        self.assertIsNotNone(COMPARISON.search("请假四天究竟是部门负责人还是HR负责人审批？"))
