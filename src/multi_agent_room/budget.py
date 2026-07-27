"""M8a：房间预算（T-M8a-02）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetConfig:
    max_patches_per_round: int = 20
    max_r2: int = 3
    max_confirm_churn: int = 3  # 对齐 spec 默认 3


@dataclass
class Budget:
    config: BudgetConfig
    patches_this_round: int = 0
    r2_count: int = 0
    confirm_index: int = 0
    stopped: bool = False
    stop_reason: str = ""

    def reset_round(self) -> None:
        self.patches_this_round = 0
        self.r2_count = 0
        self.confirm_index = 0
        self.stopped = False
        self.stop_reason = ""

    def record_patch(self) -> tuple[bool, str]:
        """登记实质补丁；超限则停循环。返回 (allowed, message)。"""
        if self.stopped:
            return False, self.stop_reason or "预算已停循环"
        self.patches_this_round += 1
        if self.patches_this_round > self.config.max_patches_per_round:
            self.stopped = True
            self.stop_reason = (
                f"超补丁预算：本轮 {self.patches_this_round}/"
                f"{self.config.max_patches_per_round}，已停止并升用户"
            )
            return False, self.stop_reason
        return True, "ok"

    def record_r2(self) -> tuple[bool, str]:
        if self.stopped:
            return False, self.stop_reason or "预算已停循环"
        self.r2_count += 1
        if self.r2_count > self.config.max_r2:
            self.stopped = True
            self.stop_reason = (
                f"超 R2 预算：{self.r2_count}/{self.config.max_r2}，已停止并升用户"
            )
            return False, self.stop_reason
        return True, "ok"

    def open_confirm_round(self) -> tuple[bool, str]:
        """确认轮 +1；达封顶则应升用户。"""
        if self.stopped:
            return False, self.stop_reason or "预算已停循环"
        self.confirm_index += 1
        if self.confirm_index > self.config.max_confirm_churn:
            self.stopped = True
            self.stop_reason = (
                f"确认轮已封顶：confirmIndex={self.confirm_index}/"
                f"{self.config.max_confirm_churn}，升用户"
            )
            return False, self.stop_reason
        return True, "ok"

    def raise_confirm_cap(self, extra: int = 2) -> None:
        """用户提额后续跑（Escalation 选项 c）。"""
        self.config.max_confirm_churn += max(1, extra)
        self.stopped = False
        self.stop_reason = ""
