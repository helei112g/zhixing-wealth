from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal,Any
from ..common import DomainError

class ResolveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=16)

class AddRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    resolve_token:str
    request_id:str=Field(min_length=1,max_length=160)
    expected_research_revision:int=Field(ge=0,strict=True)


Scope=Literal['quotes','fx','index_prices','memberships','valuations','financials']

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:Scope
    enabled:StrictBool
    cadence:Literal['daily','trade_day','weekly','manual']
    time:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone:Literal['Asia/Shanghai']='Asia/Shanghai'

class SettingsRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    policies:list[Policy]=Field(min_length=6,max_length=6)
    expected_settings_revision:int=Field(ge=0,strict=True)

class UpdateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scopes:list[Scope]=Field(default_factory=lambda:['quotes','fx','index_prices','memberships','valuations','financials'],min_length=1,max_length=6)
    request_id:str=Field(min_length=1,max_length=160)
    targets:list[str]|None=Field(None,min_length=1,max_length=100)
    resources:list[dict[str,Any]]|None=Field(None,min_length=1,max_length=200)

class ScenarioRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    analysis_id:str
    basis:Literal['price','total_return']='price'
    metric:Literal['pe','pb']='pe'
    target_multiple:str|int|float
    change_ratio:str|int|float='0'
    accept_fixed_basket_assumption:StrictBool
    reference_window:Literal[3,5,10]=5


def router(service):
    api=APIRouter(tags=['指数研究与数据更新'])
    @api.get('/api/research/revision')
    def revision():
        with service.store.lock:return dict(research_revision=service.revision(),updating=service.updating())
    @api.get('/api/research/indices')
    def listing(kind:str|None=None,page:int=Query(1,ge=1)):return service.listing(kind,page)
    @api.post('/api/research/indices/resolve')
    def resolve(body:ResolveRequest):return service.resolve(body.code)
    @api.post('/api/research/indices')
    def add(body:AddRequest):return service.add(body.model_dump())
    @api.get('/api/research/indices/{index_id}')
    def detail(index_id:str,basis:str='price',method:str='overall',window:int=3,asof:str|None=None):
        if asof is not None:raise DomainError('尚未开放严格历史时点查询；当前响应为来源当前可得版本')
        return service.detail(index_id,basis,method,window)
    @api.get('/api/research/indices/{index_id}/{part}')
    def projection(index_id:str,part:str,basis:str='price',window:int=3,method:str='overall',analysis_id:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
        if part not in ('series','constituents','financials','sources'):raise DomainError('资料模块不存在')
        return service.projection(index_id,part,basis,window,analysis_id,page,page_size,method)
    @api.post('/api/research/indices/{index_id}/scenarios')
    def scenario(index_id:str,body:ScenarioRequest):return service.scenario(index_id,body.model_dump())
    @api.get('/api/settings/data-updates')
    def settings():return service.settings()
    @api.put('/api/settings/data-updates')
    def save_settings(body:SettingsRequest):return service.save_settings(body.model_dump())
    @api.post('/api/data-updates/run',status_code=202)
    def run(body:UpdateRequest):return service.run(body.model_dump(exclude_none=True))
    @api.get('/api/data-updates/jobs/{job_id}')
    def job(job_id:str):return service.updates.get(job_id)
    @api.get('/api/data-updates/jobs')
    def jobs():return {'jobs':service.jobs()}
    return api
