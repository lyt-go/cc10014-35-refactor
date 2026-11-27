"""变更签证领域服务：签认签证后直接更新施工排期里程碑，并把费用结果同步到费用调整台账。

金额判定不再在本服务内单独维护，统一走 ``fee_rules``；费用增减的落点由
「直接塞一条修缮支出行」改为「费用调整台账」，财务仍通过统一资源入口核对
可冲减金额。零元变更不产生费用行，也不写台账。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.fee_adjustment import fee_adjustment_service
from app.services.fee_rules import (
    approved_total,
    check_negative_change,
    fee_link_lock,
    to_float,
)
from app.store import store


class ChangeOrderService(BaseModuleService):
    MODULE = "change_order"
    ENTITY = "变更签证单"
    OPERATION_TERM = "提出变更"
    LIST_FIELDS = ["变更事项", "变更原因", "影响工序", "费用增减", "提出人", "审批人", "签认日期", "备注说明"]
    REQUIRED_FIELDS = ["变更事项", "变更原因", "影响工序"]
    STATUS_ORDER = ["待提出", "已提出", "已签认", "已驳回"]
    ACTION_RULES = {"提出变更": "已提出", "签认变更": "已签认", "驳回变更": "已驳回"}
    NEGATIVE_ACTIONS = ["驳回变更"]

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "签认变更":
            process = entry.get("影响工序", "")
            # 1. 校验被影响的工序/排期确实存在
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            if not schedules:
                return False, f"受影响工序「{process}」在施工排期中不存在"
            # 2. 不能对已完成验收的工序签认变更
            accs = store.query_by_field("acceptance_record", "验收工序", process)
            if any(a.get("status") == "已签署" for a in accs):
                return False, f"工序「{process}」已有签署验收，不能签认变更"
            # 3. 费用增减为负时，不能超过当时参与计算的已核准款项（统一判定入口）
            passed, reason, _evidence = check_negative_change(entry)
            if not passed:
                return False, reason
        return True, ""

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """签认动作与费用台账写入放进同一费用联动临界区。

        这样「核准支出」与「签认变更」即使并发，也只能串行进入费用判定与
        台账写入：回执返回前台账调整项一定已经落账，不会出现财务已确认
        冲减却找不到调整项；同一份减项签证重试也只记一次调整。
        """
        if action != "签认变更":
            return super().run_action(entry_id, action)

        with fee_link_lock:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

            # 在临界区内重新做一次金额判定，拿到当时参与计算的已核准款项快照
            passed, reason, evidence_rows = check_negative_change(entry)
            if not passed:
                # 按原门槛记录拒绝，财务可在台账看到拒绝原因并等待下一次签认
                fee_adjustment_service.record_sign_result(
                    entry_id, entry,
                    passed=False, reason=reason,
                    evidence_rows=evidence_rows,
                    approved_total_amount=approved_total(),
                )
                return None, reason

            result_entry, message = super().run_action(entry_id, action)
            if result_entry is None:
                # 幂等重复等场景：若已有唯一生效调整，直接沿用，不新增费用行
                existing = fee_adjustment_service.effective_adjustment(entry_id)
                if existing is not None and to_float(entry.get("费用增减", 0)) != 0:
                    return entry, message
                return result_entry, message

            # 签认成功后再登记台账；零元变更不新增费用行
            if to_float(entry.get("费用增减", 0)) != 0:
                fee_adjustment_service.record_sign_result(
                    entry_id, entry,
                    passed=True, reason="",
                    evidence_rows=evidence_rows,
                    approved_total_amount=approved_total(),
                )
            return result_entry, message

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "签认变更":
            process = entry.get("影响工序", "")
            # 1. 更新施工排期里程碑节点
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process:
                    sched["里程碑节点"] = f"变更签证#{entry_id}：{entry.get('变更事项', '')}"
                    sched["abnormal"] = True
                    effects.append(dict(sched))

            # 2. 费用结果统一同步到费用调整台账（见 run_action 临界区），
            #    零元变更不新增费用行，这里不再直接生成修缮支出记录。

            # 3. 更新关联工序异常标记
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
        return effects
