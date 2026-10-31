"""Stable machine-readable causes for local market refresh jobs."""
TRIGGERS = {
    'startup': '启动补更', 'schedule': '定时更新',
    'manual_update': '手动更新数据', 'manual_instrument': '手动更新标的',
    'manual_full': '手动同步全部历史', 'retry': '失败后重试',
    'event_create': '新增流水后同步', 'event_edit': '更正流水后同步',
    'portfolio_create': '创建组合后同步基准', 'portfolio_edit': '编辑组合后同步基准',
    'import': '导入流水后同步', 'api_request': '接口请求',
}


def trigger_metadata(triggers, queued=False):
    reasons=sorted(set(triggers))
    return {'trigger': reasons[0] if len(reasons)==1 else 'multiple', 'triggers': reasons,
            'queued': queued, 'trigger_label': ('排队补更 · ' if queued else '')+'、'.join(TRIGGERS[t] for t in reasons)}
