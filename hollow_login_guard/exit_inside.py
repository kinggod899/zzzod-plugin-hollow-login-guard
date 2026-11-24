"""从空洞类玩法局内退出来，回到大世界。

链路：打开暂停菜单 -> 点退出战斗 -> 确认玩法专用提示 -> 结算点继续 ->
结算仓库点返回研究站 -> 确认离开局内。

为什么不用 Operation.execute()：
本操作由登录节点同步调用，宿主操作正在自己的节点循环里。execute() 的循环
遇到暂停/停止状态会 sleep(1) 空转，嵌套进来会把整个登录流程卡死。
所以这里自己跑一个有时限的循环，每轮自己截图，不碰宿主的轮次状态。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from one_dragon.utils.log_utils import log
from zzz_od.operation.zzz_operation import ZOperation

from .inside_screen import SCREEN_NAME, is_in_hollow_inside

if TYPE_CHECKING:
    from zzz_od.context.zzz_context import ZContext

TOTAL_TIMEOUT: float = 120.0
"""整条退出链的总时长上限，超时按失败上报。"""
ROUND_WAIT: float = 0.6
"""每轮之间的间隔，给菜单动画留时间。"""
RE_CLICK_INTERVAL: float = 3.0
"""点过一次后至少等这么久，画面还没变化才补点。"""
MAX_RE_CLICKS: int = 2
"""同一处最多补点几次；仍无效就停止，避免无限循环。"""
MAX_MENU_PRESSES: int = 3
"""打开暂停菜单最多按几次 Esc。"""


class ExitHollowInside(ZOperation):
    """把卡在空洞类玩法局内的角色带回大世界。"""

    def __init__(self, ctx: ZContext) -> None:
        """只做初始化；真正的循环在 run() 里，不走 execute()。"""
        super().__init__(
            ctx,
            op_name='退出空洞局内',
            timeout_seconds=-1,
            need_check_game_win=False,
        )
        self.menu_presses: int = 0
        self.confirm_clicked_at: float = 0.0
        self.confirm_retry: int = 0
        self.continue_clicked_at: float = 0.0
        self.continue_retry: int = 0

    def _has(self, area_name: str) -> bool:
        """核对画面里的区域是否出现。"""
        return self.round_by_find_area(self.last_screenshot, SCREEN_NAME, area_name).is_success

    def _refresh(self) -> None:
        """重新截图，避免用宿主的旧帧做多步判断。"""
        self.last_screenshot_time, self.last_screenshot = self.ctx.controller.screenshot()

    def _click(self, area_name: str) -> bool:
        """点一个区域；点成功返回 True。"""
        result = self.round_by_click_area(SCREEN_NAME, area_name)
        return result.is_success

    def _back_to_world(self) -> tuple[bool, str]:
        """从研究站回到大世界。一条龙只认大世界，停在研究站等于没退出。"""
        from zzz_od.operation.back_to_normal_world import BackToNormalWorld

        # 宿主操作正在自己的节点循环里。若此时全局处于暂停态，
        # 子操作的 execute() 会 sleep 空转，这里先等到恢复再跑。
        waited = 0.0
        while self.ctx.run_context.is_context_pause and waited < 60:
            time.sleep(0.5)
            waited += 0.5
        if self.ctx.run_context.is_context_stop:
            return False, '运行已停止'

        log.info('已离开结算仓库，开始返回大世界')
        result = BackToNormalWorld(
            self.ctx, ensure_normal_world=True, timeout_seconds=60,
        ).execute()
        if not result.success:
            log.error('退出空洞局内后返回大世界失败：%s', result.status)
            return False, f'返回大世界失败：{result.status}'
        return True, '已回到大世界'

    def _is_warehouse(self) -> bool:
        """是不是结算仓库界面。

        「放入仓库」和「返回研究站」两个按钮同时可见才认，单独一个在过渡画面
        上也可能闪出来，会误判。
        """
        return self._has('仓库-放入仓库') and self._has('仓库-返回研究站')

    def _leave_warehouse(self) -> bool:
        """点返回研究站并确认仓库界面消失；失败时按 Esc 再兜一次。"""
        for attempt in range(MAX_RE_CLICKS + 1):
            self._click('仓库-返回研究站')
            time.sleep(1.2)
            self._refresh()
            if not self._is_warehouse():
                return True
            if attempt < MAX_RE_CLICKS:
                log.info('仓库界面未消失，按 Esc 兜底第 %s 次', attempt + 1)
                self.ctx.controller.btn_press('esc', press_time=0.1)
                time.sleep(1.2)
                self._refresh()
                if not self._is_warehouse():
                    return True
        return False

    def _throttle(self, clicked_at: float, retry: int, label: str) -> tuple[bool, int]:
        """同一处点击的节流：返回 (现在是否可以点, 更新后的补点次数)。"""
        if clicked_at <= 0:
            return True, retry
        if time.time() - clicked_at < RE_CLICK_INTERVAL:
            return False, retry
        if retry >= MAX_RE_CLICKS:
            return False, retry
        log.info('%s 未生效，补点第 %s 次', label, retry + 1)
        return True, retry + 1

    def run(self) -> tuple[bool, str]:
        """跑完整条退出链；返回 (是否成功, 状态说明)。"""
        start = time.time()
        while time.time() - start < TOTAL_TIMEOUT:
            self._refresh()

            # 结算仓库：右下角是「ESC 返回研究站」。点完必须确认界面真的走了，
            # 否则会停在这个界面上反复点，表现就是卡死在仓库。
            if self._is_warehouse():
                if not self._leave_warehouse():
                    return False, '结算仓库点返回研究站后仍未离开'
                # 点返回研究站只是离开仓库，角色还在研究站，不是大世界。
                # 交给通用的返回大世界操作，一条龙的本体识别只认大世界。
                time.sleep(1.0)
                return self._back_to_world()

            if self._has('结算-继续'):
                if self.continue_clicked_at > 0:
                    can_click, self.continue_retry = self._throttle(
                        self.continue_clicked_at, self.continue_retry, '结算继续')
                    if not can_click and self.continue_retry >= MAX_RE_CLICKS:
                        return False, '结算继续点击后画面仍未变化'
                    if not can_click:
                        time.sleep(ROUND_WAIT)
                        continue
                if self._click('结算-继续'):
                    self.continue_clicked_at = time.time()
                time.sleep(ROUND_WAIT)
                continue

            if self._has('退出确认-提示'):
                if self.confirm_clicked_at > 0:
                    can_click, self.confirm_retry = self._throttle(
                        self.confirm_clicked_at, self.confirm_retry, '退出确认')
                    if not can_click and self.confirm_retry >= MAX_RE_CLICKS:
                        return False, '退出确认点击后仍未关闭'
                    if not can_click:
                        time.sleep(ROUND_WAIT)
                        continue
                if self._click('退出确认-确认'):
                    self.confirm_clicked_at = time.time()
                time.sleep(ROUND_WAIT)
                continue

            if self._has('菜单-退出战斗'):
                self._click('菜单-退出战斗')
                time.sleep(ROUND_WAIT)
                continue

            if is_in_hollow_inside(self.ctx, self.last_screenshot):
                if self.menu_presses >= MAX_MENU_PRESSES:
                    return False, f'按了 {self.menu_presses} 次 Esc 仍未打开暂停菜单'
                self.menu_presses += 1
                self.ctx.controller.btn_press('esc', press_time=0.1)
                time.sleep(1.2)
                continue

            return True, '已离开空洞局内'

        return False, '退出空洞局内超时'
