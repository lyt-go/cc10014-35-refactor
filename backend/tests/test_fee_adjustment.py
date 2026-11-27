"""费用联动收拢后的专项测试。

覆盖需求：
1. 支出核准与变更签认共用同一套金额判定，原门槛、原返回值不变；
2. 签认结果同步到费用调整台账，零元变更不新增费用行；
3. 负项签认证据能追到当时参与计算的已核准款项；
4. 断线重试时同一份减项签证只记一次调整；
5. 东段 600 已核准 + 迟到 400 核准与 -700 签认并发，两种推迟时序复验，
   不允许出现「财务确认 700 元冲减却找不到调整项」；
6. 100 元预算 / 110 元实际 / 零元签证的原返回值对照。
"""
from __future__ import annotations

import threading

import pytest

from app.services import change_order as change_order_module
from app.services import funding_use as funding_use_module
from app.services.change_order import ChangeOrderService
from app.services.fee_adjustment import fee_adjustment_service
from app.services.fee_rules import LEDGER_EFFECTIVE, LEDGER_REJECTED
from app.services.funding_use import FundingUseService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    from app.seed import SEED_ROWS
    for name in list(store._tables.keys()):
        if name in SEED_ROWS:
            store._tables[name] = [dict(row) for row in SEED_ROWS[name]]
        else:
            store._tables[name] = []
    # 费用联动需要精确金额口径，清掉种子里既有的支出与签证，避免互相干扰
    store._tables["funding_use"] = []
    store._tables["change_order"] = []
    store._tables["fee_adjustment"] = []
    yield


funding_service = FundingUseService()
change_service = ChangeOrderService()


# ── 构造辅助 ──
def make_funding(budget, actual, status="已提交"):
    entry, missing = funding_service.create_entry({
        "支出项目": f"测试支出{budget}/{actual}",
        "支出类别": "木料采购",
        "预算金额": budget,
        "实际金额": actual,
        "支出日期": "2026-11-20",
        "经办人": "会计甲",
    })
    assert missing == []
    if status == "已提交":
        funding_service.run_action(entry["id"], "提交报销")
    return entry


def approve(entry_id):
    return funding_service.run_action(entry_id, "核准支出")


def make_change(process, fee_change):
    # 排期与验收前置：排期存在且没有已签署验收
    if not store.query_by_field("construction_schedule", "计划工序", process):
        from app.services.construction_schedule import ConstructionScheduleService
        s = ConstructionScheduleService()
        row, missing = s.create_entry({
            "计划工序": process, "开始日期": "2026-11-01", "结束日期": "2026-12-01",
        })
        assert missing == []
        s.run_action(row["id"], "发布排期")
    change, missing = change_service.create_entry({
        "变更事项": f"{process}费用调整",
        "变更原因": "现场核量",
        "影响工序": process,
        "费用增减": fee_change,
        "签认日期": "2026-11-21",
        "审批人": "监理乙",
    })
    assert missing == []
    change_service.run_action(change["id"], "提出变更")
    return change


def sign(change_id):
    return change_service.run_action(change_id, "签认变更")


class GatedRLock:
    """测试用锁：把指定的「挂起线程」拦在锁门外（不持有内层锁），其他线程可自由获锁。

    这样可以让核准线程在费用联动临界区门口排队，而签认线程先获锁完成判定，
    精确复现「两份提交都在途、各自推迟入账/回执时点」的并发场景。
    线程 ident 在 start 之后才有值，因此持有线程对象、acquire 时动态读取；
    armed 为 False 时退化为普通 RLock，避免替换瞬间误伤。
    """

    def __init__(self, hold_thread: threading.Thread):
        self._inner = threading.RLock()
        self._hold_thread = hold_thread
        self.armed = False
        self.waiting_event = threading.Event()   # 挂起线程已到门口
        self.pass_event = threading.Event()      # 放行挂起线程
        self.entered_event = threading.Event()   # 挂起线程放行后已真正获锁

    def acquire(self, *args, **kwargs):
        if self.armed and threading.get_ident() == self._hold_thread.ident:
            self.waiting_event.set()
            self.pass_event.wait(timeout=10)     # 锁门外排队，不占内层锁
        acquired = self._inner.acquire(*args, **kwargs)
        if self.armed and threading.get_ident() == self._hold_thread.ident:
            self.entered_event.set()
        return acquired

    def release(self):
        self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()


def install_gated_lock(hold_thread: threading.Thread) -> GatedRLock:
    """把费用联动锁替换成闸门锁并布防，返回闸门对象；返回原锁供还原。"""
    gated = GatedRLock(hold_thread)
    funding_use_module.fee_link_lock = gated
    change_order_module.fee_link_lock = gated
    gated.armed = True
    return gated


def restore_lock(real_lock) -> None:
    funding_use_module.fee_link_lock = real_lock
    change_order_module.fee_link_lock = real_lock


def ledger_rows(change_id):
    return fee_adjustment_service.rows_for_change(change_id)


def effective_rows(change_id):
    return [r for r in ledger_rows(change_id) if r["status"] == LEDGER_EFFECTIVE]


# ═══════════════════════════════════════════════════════════════════
# 1. 统一金额判定：超预算 10% 门槛与原返回值不变
# ═══════════════════════════════════════════════════════════════════

def test_approval_over_budget_10pct_needs_visa_original_message():
    # 预算 100，实际 111（>110），无签证 -> 沿用原文案拒绝
    entry = make_funding(100, 111)
    updated, msg = approve(entry["id"])
    assert updated is None
    assert msg == f"超出预算{111 - 100:.2f}元，需变更签证支撑"
    assert store.find("funding_use", entry["id"])["status"] == "已提交"


def test_approval_within_10pct_passes_without_visa():
    # 预算 100，实际 110（恰好 10%）不需要签证
    entry = make_funding(100, 110)
    updated, msg = approve(entry["id"])
    assert updated is not None
    assert updated["status"] == "已核准"


def test_approval_over_budget_supported_by_signed_positive_visa():
    make_change("大木作", 20)
    sign(store.rows("change_order")[-1]["id"])
    entry = make_funding(100, 115)  # 超 15，有 +20 签证支撑
    updated, msg = approve(entry["id"])
    assert updated is not None
    assert updated["status"] == "已核准"


# ═══════════════════════════════════════════════════════════════════
# 2. 100/110/零元签证的原返回值对照
# ═══════════════════════════════════════════════════════════════════

def test_budget_100_actual_110_zero_visa_original_returns():
    # 零元签证本身可以正常签认（不触发费用行）
    change = make_change("瓦作", 0)
    signed, sign_msg = sign(change["id"])
    assert signed is not None
    assert signed["status"] == "已签认"
    assert sign_msg == "变更签证单已签认变更"

    # 100 预算 / 110 实际：恰在 10% 内，零元签证不影响核准，返回值与原行为一致
    entry = make_funding(100, 110)
    approved_entry, approve_msg = approve(entry["id"])
    assert approved_entry is not None
    assert approved_entry["status"] == "已核准"

    # 零元变更不新增费用行：台账无记录、支出表行数不增加
    assert ledger_rows(change["id"]) == []
    assert all(r.get("备注说明") != f"由变更签证#{change['id']}自动触发"
               for r in store.rows("funding_use"))


# ═══════════════════════════════════════════════════════════════════
# 3. 负项签认证据可追溯到当时参与计算的已核准款项
# ═══════════════════════════════════════════════════════════════════

def test_negative_change_evidence_traces_approved_funding():
    f1 = make_funding(400, 400)
    f2 = make_funding(200, 200)
    approve(f1["id"])
    approve(f2["id"])

    change = make_change("石作", -500)
    signed, msg = sign(change["id"])
    assert signed is not None, msg

    effective = effective_rows(change["id"])
    assert len(effective) == 1
    row = effective[0]
    assert row["调整金额"] == -500
    assert row["可冲减金额"] == 500
    assert row["已核准合计"] == 600
    evidence_ids = {item["支出记录ID"] for item in row["证据款项"]}
    assert evidence_ids == {f1["id"], f2["id"]}
    assert all(item["审批状态"] == "已核准" for item in row["证据款项"])


def test_negative_change_over_approved_total_rejected_and_logged():
    f1 = make_funding(600, 600)
    approve(f1["id"])

    change = make_change("东段木作", -700)
    signed, msg = sign(change["id"])
    assert signed is None
    assert msg == "费用扣减超过已核准总支出"
    # 签证状态保持已提出，等待下一次签认
    assert store.find("change_order", change["id"])["status"] == "已提出"

    rows = ledger_rows(change["id"])
    assert len(rows) == 1
    assert rows[0]["status"] == LEDGER_REJECTED
    assert rows[0]["处理结论"] == "费用扣减超过已核准总支出"
    # 拒绝不产生可冲减金额
    assert rows[0]["可冲减金额"] == 0
    # 财务结算视角：不可结算，看到按原门槛记录的拒绝
    view = fee_adjustment_service.settlement_view(change["id"])
    assert view["可结算"] is False
    assert view["调整"] is None
    assert "费用扣减超过已核准总支出" in view["提示"]


# ═══════════════════════════════════════════════════════════════════
# 4. 断线重试：同一份减项签证重复提交只记一次调整
# ═══════════════════════════════════════════════════════════════════

def test_duplicate_negative_change_submission_records_once():
    f1 = make_funding(1000, 1000)
    approve(f1["id"])
    change = make_change("彩画作", -300)

    first, msg1 = sign(change["id"])
    assert first is not None
    assert len(effective_rows(change["id"])) == 1

    # 模拟客户端断线后重试：清掉小时窗口幂等标记，再次提交同一份签证
    store._idempotent_log.clear()
    second, _msg2 = sign(change["id"])
    assert second is not None

    # 同一份减项签证只记一次调整，可冲减金额不翻倍（幂等窗口之外由台账兜底）
    assert len(effective_rows(change["id"])) == 1
    assert sum(r["可冲减金额"] for r in effective_rows(change["id"])) == 300
    # 不向修缮支出表塞入自动费用行
    assert all(r.get("备注说明") != f"由变更签证#{change['id']}自动触发"
               for r in store.rows("funding_use"))


# ═══════════════════════════════════════════════════════════════════
# 5. 东段并发场景：600 已核准 + 迟到 400 核准 与 -700 签认
# ═══════════════════════════════════════════════════════════════════

def _setup_east_section():
    approved = make_funding(600, 600)
    approve(approved["id"])
    late = make_funding(400, 400)  # 已提交，等待另一会计核准
    change = make_change("东段木作", -700)
    return approved, late, change


def test_concurrency_sign_first_approval_delayed():
    """推迟核准入账：核准线程先被闸在锁上，-700 先按 600 口径被拒并留痕，
    放行后 400 才核准入账，再下一次签认才生效。"""
    _approved, late, change = _setup_east_section()
    results: dict = {}
    sign_finished = threading.Event()

    t_approve = threading.Thread(
        target=lambda: results.setdefault("approve", approve(late["id"])))
    real_lock = funding_use_module.fee_link_lock
    gated = install_gated_lock(t_approve)
    try:
        t_sign = threading.Thread(
            target=lambda: (
                results.setdefault("sign1", sign(change["id"])),
                sign_finished.set()))
        t_approve.start()
        assert gated.waiting_event.wait(5)   # 核准已在锁门外排队（未占锁）
        t_sign.start()
        assert sign_finished.wait(5)         # 签认先获锁完成（核准仍在门外，两份提交均在途）
        gated.pass_event.set()               # 放行迟到的 400 核准
        t_approve.join(10)
    finally:
        restore_lock(real_lock)

    # 首次签认按原门槛（已核准仅 600）拒绝，且没有任何已生效调整
    assert results["sign1"][0] is None
    assert results["sign1"][1] == "费用扣减超过已核准总支出"
    assert effective_rows(change["id"]) == []
    rejected = [r for r in ledger_rows(change["id"]) if r["status"] == LEDGER_REJECTED]
    assert len(rejected) == 1
    assert rejected[0]["已核准合计"] == 600

    # 迟到核准此时已入账
    assert results["approve"][0] is not None
    assert store.find("funding_use", late["id"])["status"] == "已核准"

    # 下一次签认：凭唯一已生效调整继续结算
    retry, retry_msg = sign(change["id"])
    assert retry is not None, retry_msg
    effective = effective_rows(change["id"])
    assert len(effective) == 1
    assert effective[0]["可冲减金额"] == 700
    assert effective[0]["已核准合计"] == 1000
    view = fee_adjustment_service.settlement_view(change["id"])
    assert view["可结算"] is True
    assert view["调整"]["id"] == effective[0]["id"]


def test_concurrency_approval_first_sign_receipt_delayed():
    """推迟签认发回执：400 核准先获锁入账（合计 1000）并释放锁，-700 签认随后获锁，
    一次成功且只落一条调整。"""
    _approved, late, change = _setup_east_section()
    results: dict = {}
    approval_done = threading.Event()

    t_approve = threading.Thread(
        target=lambda: (
            results.setdefault("approve", approve(late["id"])),
            approval_done.set()))
    real_lock = funding_use_module.fee_link_lock
    gated = install_gated_lock(t_approve)
    try:
        t_approve.start()
        assert gated.waiting_event.wait(5)    # 核准在锁门外排队
        gated.pass_event.set()                # 放行
        assert gated.entered_event.wait(5)    # 确认核准已真正获锁
        # 此刻才发起签认：核准要么仍在临界区（签认排队），要么刚释放（签认获锁），
        # 两种情况下签认读到的已核准合计都已是 1000
        t_sign = threading.Thread(
            target=lambda: results.setdefault("sign", sign(change["id"])))
        t_sign.start()
        assert approval_done.wait(10)         # 400 先入账
        t_approve.join(10)
        t_sign.join(10)
    finally:
        restore_lock(real_lock)

    # 400 已先入账，-700 在合计 1000 的口径下一次签认成功
    assert results["approve"][0] is not None
    assert store.find("funding_use", late["id"])["status"] == "已核准"
    signed, sign_msg = results["sign"]
    assert signed is not None, sign_msg

    effective = effective_rows(change["id"])
    assert len(effective) == 1
    assert effective[0]["调整金额"] == -700
    assert effective[0]["可冲减金额"] == 700
    assert effective[0]["已核准合计"] == 1000
    # 回执返回前台账调整项已落账，绝不允许「确认了 700 冲减却找不到调整项」
    assert fee_adjustment_service.effective_adjustment(change["id"]) is not None
    view = fee_adjustment_service.settlement_view(change["id"])
    assert view["可结算"] is True

    # 再重试一次仍然只有一条
    store._idempotent_log.clear()
    again, _ = sign(change["id"])
    assert again is not None
    assert len(effective_rows(change["id"])) == 1



def test_concurrent_duplicate_sign_under_lock_only_one_adjustment():
    """两份在途签认同时到达：费用联动锁保证只落一条已生效调整。"""
    _approved, late, change = _setup_east_section()
    approve(late["id"])  # 已核准合计 1000，-700 可通过

    outcomes = []

    def worker():
        outcomes.append(sign(change["id"]))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(10); t2.join(10)

    assert len(outcomes) == 2
    assert all(entry is not None for entry, _ in outcomes)
    effective = effective_rows(change["id"])
    assert len(effective) == 1
    assert effective[0]["可冲减金额"] == 700


# ═══════════════════════════════════════════════════════════════════
# 6. 台账为系统自动登记，封手工入口；正向调整同步台账但无可冲减金额
# ═══════════════════════════════════════════════════════════════════

def test_ledger_rejects_manual_create_and_action():
    entry, missing = fee_adjustment_service.create_entry({"调整金额": 100})
    assert entry is None
    assert missing and "自动登记" in missing[0]
    result, msg = fee_adjustment_service.run_action(1, "任意动作")
    assert result is None
    assert "自动登记" in msg


def test_positive_change_ledger_has_no_offset_amount():
    change = make_change("油漆作", 300)
    signed, msg = sign(change["id"])
    assert signed is not None
    effective = effective_rows(change["id"])
    assert len(effective) == 1
    assert effective[0]["调整金额"] == 300
    assert effective[0]["可冲减金额"] == 0
