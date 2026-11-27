"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.building_survey import BuildingSurveyService
from app.services.restoration_plan import RestorationPlanService
from app.services.material_verify import MaterialVerifyService
from app.services.craftsman_assign import CraftsmanAssignService
from app.services.work_sequence import WorkSequenceService
from app.services.quality_inspect import QualityInspectService
from app.services.historical_drawing import HistoricalDrawingService
from app.services.component_registry import ComponentRegistryService
from app.services.env_monitor import EnvMonitorService
from app.services.restoration_log import RestorationLogService
from app.services.acceptance_record import AcceptanceRecordService
from app.services.funding_use import FundingUseService
from app.services.fee_adjustment import FeeAdjustmentService
from app.services.archive_mgmt import ArchiveMgmtService
from app.services.expert_review import ExpertReviewService
from app.services.safety_briefing import SafetyBriefingService
from app.services.construction_schedule import ConstructionScheduleService
from app.services.change_order import ChangeOrderService
from app.services.completion_summary import CompletionSummaryService

SERVICES: dict[str, BaseModuleService] = {
    "building_survey": BuildingSurveyService(),
    "restoration_plan": RestorationPlanService(),
    "material_verify": MaterialVerifyService(),
    "craftsman_assign": CraftsmanAssignService(),
    "work_sequence": WorkSequenceService(),
    "quality_inspect": QualityInspectService(),
    "historical_drawing": HistoricalDrawingService(),
    "component_registry": ComponentRegistryService(),
    "env_monitor": EnvMonitorService(),
    "restoration_log": RestorationLogService(),
    "acceptance_record": AcceptanceRecordService(),
    "funding_use": FundingUseService(),
    "fee_adjustment": FeeAdjustmentService(),
    "archive_mgmt": ArchiveMgmtService(),
    "expert_review": ExpertReviewService(),
    "safety_briefing": SafetyBriefingService(),
    "construction_schedule": ConstructionScheduleService(),
    "change_order": ChangeOrderService(),
    "completion_summary": CompletionSummaryService(),
}


SERVICE_INSTANCES = SERVICES

def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
