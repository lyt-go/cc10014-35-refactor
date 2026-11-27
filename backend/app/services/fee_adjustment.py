"""费用调整台账：变更签证签认结果的唯一权威落点，财务按原资源入口核对可冲减金额。

台账不接受手工登记或手工动作，只由变更签证签认流程在费用联动临界区内写入：

- 签认通过：按来源签证写入唯一一条「已生效」调整，同一签证断线重试也只记一次；
- 签认拒绝：按原门槛写入「已拒绝」记录并保留拒绝原因，等待下一次签认；
- 零元变更：不产生任何费用行，自然也不会写入台账。

财务仍走统一资源路由（列表/导出/详情）核对，可冲减金额只对已生效的负项
调整有值，且证据款项能追溯到签认当时参与计算的每一笔已核准支出。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.fee_rules import (
    LEDGER_EFFECTIVE,
    LEDGER_MODULE,
    LEDGER_REJECTED,
    to_float,
)
from app.store import store


class FeeAdjustmentService(BaseModuleService):
    MODULE = LEDGER_MODULE
    ENTITY = "费用调整台账"
    OPERATION_TERM = ""
    LIST_FIELDS = [
        "来源签证", "影响工序", "变更事项", "调整金额", "可冲减金额",
        "已核准合计", "处理结论", "证据款项", "签认日期", "备注说明",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER = [LEDGER_EFFECTIVE, LEDGER_REJECTED]
    ACTION_RULES: dict[str, str] = {}

    # ── 台账为系统自动登记，封掉手工入口 ──
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        return None, ["费用调整台账由变更签证签认自动登记，不接受手工录入"]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return None, "费用调整台账为系统自动登记，不支持手工执行动作"

    # ── 台账查询辅助 ──
    def rows_for_change(self, change_id: int) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if row.get("来源签证ID") == change_id
        ]

    def effective_adjustment(self, change_id: int) -> dict[str, Any] | None:
        """返回某份签证唯一的已生效调整；不存在返回 None。"""
        for row in self.rows_for_change(change_id):
            if row.get("status") == LEDGER_EFFECTIVE:
                return row
        return None

    def latest_rejection(self, change_id: int) -> dict[str, Any] | None:
        """返回某份签证最近一次按原门槛记录的拒绝。"""
        rejections = [
            row for row in self.rows_for_change(change_id)
            if row.get("status") == LEDGER_REJECTED
        ]
        return rejections[-1] if rejections else None

    def settlement_view(self, change_id: int) -> dict[str, Any]:
        """财务结算视角：要么凭唯一已生效调整继续，要么看到拒绝并等下一次签认。"""
        effective = self.effective_adjustment(change_id)
        if effective is not None:
            return {
                "来源签证ID": change_id,
                "可结算": True,
                "调整": effective,
                "最近拒绝": None,
                "提示": "存在唯一已生效费用调整，可继续结算",
            }
        rejection = self.latest_rejection(change_id)
        return {
            "来源签证ID": change_id,
            "可结算": False,
            "调整": None,
            "最近拒绝": rejection,
            "提示": rejection.get("处理结论", "") if rejection else "尚无签认结果，等待签认",
        }

    # ── 签认流程唯一写入口（在 fee_link_lock 临界区内调用） ──
    def record_sign_result(
        self,
        change_id: int,
        entry: dict[str, Any],
        *,
        passed: bool,
        reason: str,
        evidence_rows: list[dict[str, Any]],
        approved_total_amount: float,
    ) -> dict[str, Any]:
        """登记一次签认结果。

        通过时同一来源签证只保留一条已生效调整（重试幂等）；拒绝时每次都
        留痕，便于财务看到按原门槛记录的拒绝原因。
        """
        fee_change = to_float(entry.get("费用增减", 0))
        existing = self.effective_adjustment(change_id) if passed else None
        if existing is not None:
            return existing

        ledger_rows = store.rows(self.MODULE)
        row: dict[str, Any] = {
            "id": max((int(r.get("id", 0)) for r in ledger_rows), default=0) + 1,
            "status": LEDGER_EFFECTIVE if passed else LEDGER_REJECTED,
            "pending": False,
            "abnormal": not passed,
            "来源签证ID": change_id,
            "来源签证": f"变更签证#{change_id}",
            "影响工序": entry.get("影响工序", ""),
            "变更事项": entry.get("变更事项", ""),
            "调整金额": fee_change,
            # 只有已生效的负项调整才形成可冲减金额
            "可冲减金额": abs(fee_change) if passed and fee_change < 0 else 0,
            "已核准合计": approved_total_amount,
            "处理结论": "签认通过，费用调整已生效" if passed else reason,
            "证据款项": [
                {
                    "支出记录ID": int(row_.get("id", 0)),
                    "支出项目": row_.get("支出项目", ""),
                    "实际金额": to_float(row_.get("实际金额", 0)),
                    "审批状态": row_.get("status", ""),
                }
                for row_ in evidence_rows
            ],
            "签认日期": entry.get("签认日期", ""),
            "备注说明": f"由变更签证#{change_id}签认自动登记",
        }
        ledger_rows.append(row)
        return row


# 便于其他服务直接引用单例，避免循环导入时反复实例化
fee_adjustment_service = FeeAdjustmentService()
