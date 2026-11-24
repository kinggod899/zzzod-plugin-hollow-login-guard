"""插件的占位应用。

补丁在插件包导入时就已经生效，这个应用本身不做任何事：它存在的意义是
让插件被应用工厂扫描到（进而加载插件画面、把补丁写进运行中的进程），
并在应用列表里留一个可勾选的条目，方便确认插件装上了。

它不建每日运行记录，选中或不选都不影响补丁是否生效。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from zzz_od.application.zzz_application import ZApplication

from . import const

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext


class HollowLoginGuardApp(ZApplication):
    """占位应用：一轮就成功结束，不碰游戏。"""

    def __init__(self, ctx: ZContext) -> None:
        ZApplication.__init__(
            self,
            ctx=ctx,
            app_id=const.APP_ID,
            op_name=const.APP_NAME,
            need_check_game_win=False,
        )

    @operation_node(name='确认补丁生效', is_start_node=True, screenshot_before_round=False)
    def confirm_patched(self) -> OperationRoundResult:
        """补丁已在插件加载时生效，这里没有要做的事。"""
        return self.round_success('防登录卡死补丁已生效')
