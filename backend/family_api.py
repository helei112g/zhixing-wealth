"""Versioned household commands share the application's session and transaction guard."""
from typing import Any, Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from .common import wire, today
from .family_service import FamilyService
from .family_notifications import FamilyNotifications


class FamilyCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['item', 'valuation', 'link', 'links', 'unlink', 'settings', 'schedule', 'reconcile', 'closure', 'income']
    payload: dict[str, Any] = Field(description='操作参数 schema 见 GET /api/family/command-schema；金额保持十进制字符串')
    expected_revision: int = Field(strict=True, ge=0)
    record_id: str | None = None


class NotificationCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['enable', 'disable', 'refresh', 'test']


def family_router(store, environment):
    family = FamilyService(store)
    notifications = FamilyNotifications(store, family, environment)
    router = APIRouter(prefix='/api/family', tags=['资产'])

    @router.get('/command-schema', summary='读取资产推荐命令的 JSON Schema、语义及回读路径')
    def command_schema():
        from .asset_contract import asset_command_contract
        return asset_command_contract()

    @router.get('/revision')
    def revision():
        with store.connection() as db:
            return {'revision': int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]), 'today': str(today())}

    @router.get('/overview')
    def overview(date: str | None = None): return family.read(date)

    @router.get('/summary')
    def summary(grain: str = 'month', period: str | None = None, start: str | None = None, end: str | None = None):
        return family.summary(grain, period, start, end)

    @router.get('/catalog')
    def catalog(): return wire(family.catalog())

    @router.get('/history', summary='月度比上月末、年度比上年末（previous）；显式 yoy 为去年同期')
    def history(start: str | None = None, end: str | None = None, grain: str = 'month', comparison: str = 'previous'):
        return family.history(start, end, grain, comparison)

    @router.get('/income', summary='读取年度收入：月度 rows、最新 records、累计与分类占比 summary', description='summary 含 total/known_total/recorded_months/missing_count 和 groups；每组含 amount/share。金额为 Decimal 字符串，share 为 0–1 比例；无记录、缺汇率或非正分母保留未知，详见 docs/api.md。')
    def income(year: int | None = None): return family.income_summary(year)

    @router.get('/reminders')
    def reminders(): return dict(family.reminders(), notification=notifications.status())

    @router.get('/notifications')
    def notification_status(): return notifications.status()

    @router.post('/notifications')
    def notification_action(command: NotificationCommand): return notifications.action(command.action)

    @router.post('/preview')
    def preview(command: FamilyCommand): return family.command(**command.model_dump(), preview=True)

    @router.post('/commands')
    def write(command: FamilyCommand):
        result = family.command(**command.model_dump())
        notifications.invalidate()
        return result

    return router, notifications
