from typing import Literal

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

Method = Literal['mcw', 'ew', 'ewpvo', 'median']
Group = Literal['full_market', 'composites', 'calendar', 'china_rates', 'us_rates', 'credit', 'repo', 'pmi', 'prices', 'money', 'social_financing']


class MarketSettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    automatic: StrictBool
    groups: list[Group] = Field(min_length=1, max_length=11)
    expected_settings_revision: int = Field(ge=0, strict=True)


class MarketUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    groups: list[Group] = Field(default_factory=list, max_length=11)
    indices: list[str] = Field(default_factory=list, max_length=10)
    full: StrictBool = False
    request_id: str = Field(min_length=1, max_length=160)


class MarketFreezeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis_id: str = Field(pattern='^[a-f0-9]{64}$')
    method: Method
    asof: str
    expected_research_revision: int = Field(ge=0, strict=True)


def router(service):
    api = APIRouter(prefix='/api/market-waterlevel', tags=['市场水位'], responses={
        409: {'description': '原研究或设置版本已变化；回读核对，不强制覆盖'},
        422: {'description': '日期、口径或来源无效；旧快照保留'},
    })

    @api.get('', summary='只读本地数据；日期截取当前可得版本，不宣称历史时点已知')
    def dashboard(method: Method = 'mcw', asof: str | None = None):
        return service.dashboard(method, asof)

    @api.get('/revision', summary='只读轻量版本；失败状态变化独立于成功发布的数据修订')
    def revision():
        return service.status()

    @api.get('/facts', summary='模型可读取的带版本事实包；无图表，不请求模型或外部数据')
    def facts(method: Method = 'mcw', asof: str | None = None):
        value = service.dashboard(method, asof)
        for item in value['comparisons'].values():
            item.pop('chart')
        for item in value['series']:
            item['statistics'].pop('chart')
        for index in value['indices']:
            for stats in index['metrics'].values():
                stats.pop('chart')
        return value

    @api.get('/settings')
    def settings():
        return service.settings()

    @api.get('/sources', summary='读取可独立更新的市场数据项、来源与最新观测日期')
    def data_sources():
        return service.data_sources()

    @api.put('/settings')
    def save_settings(body: MarketSettingsRequest):
        return service.save_settings(body.model_dump())

    @api.post('/updates', status_code=202, summary='手动增量或全量校对市场来源及共享指数估值')
    def updates(body: MarketUpdateRequest):
        return service.update(body.groups, body.request_id, full=body.full, indices=body.indices)

    @api.post('/snapshots', summary='核对分析摘要及研究版本后冻结，不重新抓取')
    def freeze(body: MarketFreezeRequest):
        return service.freeze(body.model_dump())

    @api.get('/snapshots')
    def snapshots():
        return service.reports()

    @api.get('/snapshots/{snapshot_id}')
    def frozen(snapshot_id: str):
        return service.frozen(snapshot_id)

    @api.get('/snapshots/{snapshot_id}/export', response_class=HTMLResponse)
    def export(snapshot_id: str):
        from .export import render
        snapshot = service.frozen(snapshot_id)
        return HTMLResponse(render(snapshot), headers={
            'Content-Disposition': f'attachment; filename="market-waterlevel-{snapshot["data"]["asof"]}.html"',
            'X-Content-Type-Options': 'nosniff',
        })

    return api
