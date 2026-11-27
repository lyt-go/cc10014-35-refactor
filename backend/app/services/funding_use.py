"""修缮支出领域服务：支出审批与变更签证费用联动，超出预算后联动排期异常。

金额判定统一委托 ``fee_rules``，与变更签证签认共用同一套口径；核准动作与
签证签认共用费用联动临界区锁，保证并发时「已核准款项 → 台账调整」的读取
和写入是串行一致的。原有的输入输出、报错文案和审批门槛保持不变。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.fee_rules import check_budget_overrun, fee_link_lock
from app.store import store


class FundingUseService(BaseModuleService):
    MODULE = "funding_use"
    ENTITY = "支出记录"
    OPERATION_TERM = "提交报销"
    LIST_FIELDS = ["支出项目", "支出类别", "预算金额", "实际金额", "支出日期", "经办人", "审批状态", "备注说明"]
    REQUIRED_FIELDS = ["支出项目", "支出类别", "预算金额"]
    STATUS_ORDER = ["待提交", "已提交", "已核准", "已驳回"]
    ACTION_RULES = {"提交报销": "已提交", "核准支出": "已核准", "驳回支出": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回支出"]

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "核准支出":
            # 超额检查（实际支出超过预算 10% 需有效签证支撑），判定口径统一收口
            passed, reason = check_budget_overrun(entry)
            if not passed:
                return False, reason
        if action == "驳回支出":
            # 驳回后检查是否有已核准的支出引用了此驳回项
            pass  # 无特殊前置校验
        return True, ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """核准支出进入费用联动临界区，与签证签认互斥。"""
        if action != "核准支出":
            return super().run_action(entry_id, action)
        with fee_link_lock:
            return super().run_action(entry_id, action)

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "核准支出":
            # 核准后更新竣工总结中的费用汇总
            for summary in store.rows("completion_summary"):
                if summary.get("status") in ("编制中", "已提交"):
                    summary["费用已核准"] = True
                    effects.append(dict(summary))
        return effects
