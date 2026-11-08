import re
import unicodedata
from dataclasses import dataclass


CONCRETE_REQUEST = re.compile(r"这笔|该笔|这一笔|一笔|本次|这次|此次|我(?:要|准备|打算|想|负责|申请|计划)")
APPROVAL_REQUEST = re.compile(r"谁.{0,8}(?:审批|批准|审核)|审批人|审批层级|哪(?:个|位|一级).{0,8}(?:审批|批准)")
QUOTE_REQUEST = re.compile(r"(?:几|多少)家.{0,14}(?:报价|比价)|(?:报价|比价).{0,10}(?:几|多少)家")
NUMBER = r"[0-9零〇一二两三四五六七八九十百千万亿半][0-9零〇一二两三四五六七八九十百千万亿半点.,，]*"
AMOUNT = re.compile(rf"(?:人民币|¥)\s*{NUMBER}|{NUMBER}\s*(?:元|块|万|亿)")
DURATION = re.compile(rf"{NUMBER}\s*(?:个)?(?:工作日|自然日|天|周|小时)")
LEAVE_THRESHOLD = re.compile(
    r"连续请假不超过(\d+)个工作日[，,]?由部门负责人审批[；;]"
    r"超过\1个工作日[，,]?由部门负责人审核后报HR负责人审批"
)


@dataclass(frozen=True)
class Clarification:
    rule: str
    answer: str
    citation_ids: list[str]


def clarify_missing_condition(
    question: str, evidence: list[dict], versions: dict[str, str],
) -> Clarification | None:
    question = unicodedata.normalize("NFKC", question)
    if not CONCRETE_REQUEST.search(question):
        return None
    approval = APPROVAL_REQUEST.search(question)
    payment = approval and re.search(r"付款|对公支付|支付款项", question)
    procurement = QUOTE_REQUEST.search(question) and re.search(r"采购|买|购置", question)
    leave = approval and re.search(r"请(?:事|病|年|婚|产)?假|休假", question)
    if sum(bool(intent) for intent in (payment, procurement, leave)) != 1:
        return None

    if payment or procurement:
        if AMOUNT.search(question):
            return None
        phrase, condition, decision, rule = (
            ("同一合同或同一事项累计应付金额", "这笔付款按同一合同或同一事项计算的累计应付金额",
             "审批人", "payment_total")
            if payment else
            ("报价数量按同一需求累计金额判断", "这笔采购按同一需求计算的累计采购金额",
             "需要几家独立供应商报价", "procurement_total")
        )
        for item in evidence:
            if phrase in item["text"]:
                ref = item["id"]
                return Clarification(
                    rule, f"请补充{condition}是多少元。缺少这一金额，暂时无法确定{decision}。[{ref}]",
                    [ref],
                )
        return None

    if DURATION.search(question):
        return None
    thresholds = {}
    for item in evidence:
        match = LEAVE_THRESHOLD.search(item["text"])
        if match:
            thresholds.setdefault(int(match[1]), item["id"])
    if not thresholds:
        return None

    ask = "请补充这次连续请假的工作日数。"
    if len(thresholds) == 2 and len({versions[ref] for ref in thresholds.values()}) == 2:
        lower, upper = sorted(thresholds)
        left, right = thresholds[lower], thresholds[upper]
        days = str(upper) if lower + 1 == upper else f"{lower + 1}至{upper}"
        return Clarification(
            "leave_workdays",
            f"{ask}若连续请假{days}个工作日，现有两份制度的审批要求冲突："
            f"一份要求部门负责人审核后报HR负责人审批[{left}]，另一份只需部门负责人审批[{right}]。",
            [left, right],
        )
    ref = next(iter(thresholds.values()))
    return Clarification(
        "leave_workdays", f"{ask}审批人取决于连续请假的时长，天数明确后才能判断适用条款。[{ref}]", [ref],
    )
