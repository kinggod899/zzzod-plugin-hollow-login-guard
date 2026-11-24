"""一条龙第三方插件：防登录卡死贝果小助手。

插件被一条龙加载时给「进入游戏」流程打补丁：角色留在贝果等空洞玩法局内时，
登录阶段直接把他带出来回到大世界，避免一条龙卡在登录节点直到超时、
后面的应用一个都跑不起来。

补丁在插件包被导入时应用。一条龙扫描插件工厂会先导入插件包的 __init__，
所以补丁早于任何应用执行；禁用或删除插件后，主程序行为回到原生。
"""

from one_dragon.utils.log_utils import log

try:
    from .login_patch import apply_patch

    apply_patch()
except Exception:
    # 不能因为补丁失败就阻止整个插件被注册，否则连日志都看不到
    log.error('防登录卡死补丁加载失败', exc_info=True)
