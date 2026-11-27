"""费用判定共享规则：收拢修缮支出核准与变更签证签认各自维护的金额判断。

历史上两处分别实现了「超预算 10% 需签证支撑」和「负项签证不得超过已核准
支出」的判断，口径容易漂移。这里统一为唯一入口，两个领域服务都调用本模块，
但保持各自原有的返回结构、报错文案与审批门槛不变。

所有函数只做只读判断，不改变任何业务表的状态；写台账的动作统一收敛到
``FeeAdjustmentService``，保证「先有调整项、后有签认回执」的次序。
"""
from __future__ import annotations

import threading
from typing import Any

from app.store import store

# 费用联动临界区锁：核准支出与签证签认都会读写「已核准支出 + 费用调整台账」，
# 两个动作分属不同资源键，资源锁无法互斥，因此这里再补一把领域锁。
fee_link_lock = threading.RLock()

BUDGET_TOLERANCE = 1.1
LEDGER_MODULE = "fee_adjustment"
LEDGER_EFFECTIVE = "已生效"
LEDGER_REJECTED = "已拒绝"


def to_float(value: Any) -> float:
    """宽松地把录入值转成金额，空值按 0 处理。"""
    if value is None or value == "":
        return 0.0
    return float(value)


def approved_funding_rows() -> list[dict[str, Any]]:
    """当前已核准的支出款项，按核准先后（id）排列，供负项证据追溯。"""
    rows = [
        row for row in store.rows("funding_use")
        if row.get("status") == "已核准"
    ]
    return sorted(rows, key=lambda row: int(row.get("id", 0)))


def approved_total() -> float:
    """已核准支出的实际金额合计。"""
    return sum(to_float(row.get("实际金额", 0)) for row in approved_funding_rows())


def signed_change_orders() -> list[dict[str, Any]]:
    """已签认且带费用增减字段的变更签证。"""
    return [
        row for row in store.rows("change_order")
        if row.get("status") == "已签认" and "费用增减" in row
    ]


def check_budget_overrun(entry: dict[str, Any]) -> tuple[bool, str]:
    """支出核准门槛：实际支出超过预算 10% 时必须有有效签证支撑。

    返回 ``(是否通过, 失败原因)``，失败文案沿用修缮支出服务的原始口径。
    """
    actual = to_float(entry.get("实际金额", 0))
    budget = to_float(entry.get("预算金额", 0))
    if actual > budget * BUDGET_TOLERANCE:
        overrun = actual - budget
        has_support = any(
            to_float(order.get("费用增减", 0)) >= overrun
            for order in signed_change_orders()
        )
        if not has_support:
            return False, f"超出预算{overrun:.2f}元，需变更签证支撑"
    return True, ""


def check_negative_change(entry: dict[str, Any]) -> tuple[bool, str, list[dict[str, Any]]]:
    """签证签认门槛：减项金额不得超过当时参与计算的已核准款项合计。

    返回 ``(是否通过, 失败原因, 参与计算的已核准款项快照)``。证据快照按
    核准款项逐条记录，负项签认后要能追溯到每一笔当时在册的已核准款项。
    """
    fee_change = to_float(entry.get("费用增减", 0))
    evidence_rows = approved_funding_rows()
    if fee_change < 0 and abs(fee_change) > approved_total():
        return False, "费用扣减超过已核准总支出", evidence_rows
    return True, "", evidence_rows
