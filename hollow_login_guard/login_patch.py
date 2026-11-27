"""给登录流程打补丁：落在空洞局内时先把角色带出去。

一条龙执行第一个应用前会插入「检测游戏窗口 -> 打开并进入游戏」节点，它调用
EnterGame。EnterGame 只认大世界画面和已知弹窗，角色留在空洞类玩法局内时
两者都不是，于是会一直重试到超时，后面的应用根本不会开始。

本补丁接管 EnterGame 的三个节点：

- check_enter_click_status（点击进入游戏）：识别到「登录成功」文本时先退出玩法。
  这是实际跑的主路径。
- check_screen（画面识别）：未知画面兜底前先退出玩法。
- wait_loading（登录成功）：进节点先判一次。

关键实现细节：框架靠函数上的两个属性建图
- operation_node_annotation：节点定义（名字、超时、op_method 指向哪个函数）
- operation_edge_annotation：这个节点有哪些入边

只替换类属性会丢掉 node 上的 op_method 指向，边注解也必须一起搬，
否则入边全部丢失、节点永远进不去。这两点是踩过的坑。

补丁只在内存里接管方法，不修改任何主程序文件，更新主程序不会覆盖。
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING

from one_dragon.base.operation.operation import Operation
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.operation.enter_game.enter_game import EnterGame

from .exit_inside import ExitHollowInside
from .inside_screen import is_in_hollow_inside

if TYPE_CHECKING:
    pass

_STATE_ATTR: str = '_hollow_login_exit_state'
"""保存原始函数与包装深度的类属性名。"""


def _exit_if_inside(op: Operation) -> bool:
    """画面是空洞局内就退出玩法；返回是否成功（不需要退出也算成功）。"""
    screen = op.last_screenshot
    if not is_in_hollow_inside(op.ctx, screen):
        return True
    log.info('识别到角色在空洞类玩法局内，先退出玩法再继续登录流程')
    ok, status = ExitHollowInside(op.ctx).run()
    if not ok:
        log.error('退出空洞局内失败：%s', status)
        return False
    log.info('已退出空洞局内：%s', status)
    return True


def _copy_attrs(src: Callable, dst: Callable) -> None:
    """把原函数上的框架元数据完整搬到包装函数上。

    node 注解里的 op_method 要改成包装函数本身，否则框架执行时仍然调用原函数；
    edge 注解（入边）必须整体搬过去，否则这个节点在图里没有入边，永远进不去。
    """
    for attr in ('operation_edge_annotation', 'node_notify_list', '__wrapped__'):
        value = getattr(src, attr, None)
        if value is not None:
            setattr(dst, attr, value)

    node = getattr(src, 'operation_node_annotation', None)
    if node is not None:
        dst.operation_node_annotation = node
        node.op_method = dst


def _wrap_check_enter_click(original: Callable) -> Callable:
    """点击进入游戏：识别到登录成功文本时，先退出玩法再按原逻辑返回。"""

    def patched(self) -> OperationRoundResult:
        return original(self)

    _copy_attrs(original, patched)
    return patched


def _wrap_check_screen(original: Callable) -> Callable:
    """画面识别：未知画面兜底前先退出玩法。"""

    def patched(self) -> OperationRoundResult:
        result = original(self)
        # 只在原逻辑准备按「未知画面」重试时才介入，其它分支保持原样。
        if result.status == '未知画面' and _exit_if_inside(self):
            return self.round_retry('已退出空洞局内', wait=1)
        return result

    _copy_attrs(original, patched)
    return patched


def _wrap_wait_loading(original: Callable) -> Callable:
    """登录成功：进节点先判一次局内。"""

    def patched(self) -> OperationRoundResult:
        if not _exit_if_inside(self):
            return self.round_fail('退出空洞局内失败')
        return original(self)

    _copy_attrs(original, patched)
    return patched


def _patched_enter_click_status(original: Callable) -> Callable:
    """点击进入游戏节点的补丁：主路径拦截。"""

    def patched(self) -> OperationRoundResult:
        result = original(self)
        # 命中「登录成功」时角色可能还在玩法局内：游戏已显示登录完成，
        # 但人不在大世界。这时先退出玩法，再照原样返回成功。
        if result.status == EnterGame.STATUS_LOGIN_SUCCESS:
            if not _exit_if_inside(self):
                return self.round_fail('退出空洞局内失败')
        return result

    _copy_attrs(original, patched)
    return patched


_WRAPPERS: dict[str, Callable[[Callable], Callable]] = {
    'check_enter_click_status': _patched_enter_click_status,
    'check_screen': _wrap_check_screen,
    'wait_loading': _wrap_wait_loading,
}


def apply_patch() -> bool:
    """给 EnterGame 的登录节点打补丁；重复调用不会叠加包裹。"""
    state: dict[str, Callable] = getattr(EnterGame, _STATE_ATTR, None) or {}
    setattr(EnterGame, _STATE_ATTR, state)

    applied: list[str] = []
    for name, wrapper in _WRAPPERS.items():
        # 原始函数只在第一次取；后续重载复用，避免套娃。
        if name not in state:
            fn = getattr(EnterGame, name, None)
            if fn is None:
                log.warning('防登录卡死补丁：EnterGame 没有节点 %s，跳过', name)
                continue
            node = getattr(fn, 'operation_node_annotation', None)
            state[name] = getattr(node, 'op_method', None) or fn

        original = state[name]
        patched = wrapper(original)
        with contextlib.suppress(AttributeError):
            patched.__signature__ = inspect.signature(original)
        setattr(EnterGame, name, patched)
        applied.append(name)

    if not applied:
        log.error('防登录卡死补丁：没有可打的节点，插件未生效')
        return False
    log.info('防登录卡死补丁已生效：%s', ', '.join(applied))
    return True
