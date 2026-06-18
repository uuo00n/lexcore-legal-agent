"""轻量 Prometheus 指标。

不引入额外依赖，使用进程内计数器导出 Prometheus text format。它适合本
项目的第一版可观测性演示；如果后续接入 OpenTelemetry，可以把这些指标
作为迁移边界。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import DefaultDict


_lock = threading.Lock()
_counters: DefaultDict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_histograms: DefaultDict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """
    函数作用：
        将标签字典转为稳定排序的 key。
    输入参数：
        - labels: dict[str, str] | None
    输出参数：
        - tuple[tuple[str, str], ...]
    """
    return tuple(sorted((labels or {}).items()))


def inc_counter(name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
    """
    函数作用：
        增加计数器。
    输入参数：
        - name: str
        - labels: dict[str, str] | None，默认值 None
        - value: float，默认值 1.0
    输出参数：
        - 无
    """
    with _lock:
        _counters[(name, _labels_key(labels))] += value


def observe(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """
    函数作用：
        记录观测值，用于导出 count/sum/avg。
    输入参数：
        - name: str
        - value: float
        - labels: dict[str, str] | None，默认值 None
    输出参数：
        - 无
    """
    with _lock:
        _histograms[(name, _labels_key(labels))].append(value)


def timed(name: str, labels: dict[str, str] | None = None):
    """
    函数作用：
        生成简单上下文计时器。
    输入参数：
        - name: str
        - labels: dict[str, str] | None，默认值 None
    输出参数：
        - 未标注
    """
    class _Timer:
        def __enter__(self):
            self.started = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            observe(name, (time.perf_counter() - self.started) * 1000, labels)

    return _Timer()


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    """
    函数作用：
        格式化 Prometheus 标签。
    输入参数：
        - labels: tuple[tuple[str, str], ...]
    输出参数：
        - str
    """
    if not labels:
        return ""
    body = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + body + "}"


def render_prometheus() -> str:
    """
    函数作用：
        导出 Prometheus text format。
    输入参数：
        - 无
    输出参数：
        - str
    """
    lines: list[str] = []
    with _lock:
        for (name, labels), value in sorted(_counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}{_format_labels(labels)} {value}")
        for (name, labels), values in sorted(_histograms.items()):
            count = len(values)
            total = sum(values)
            avg = total / count if count else 0.0
            lines.append(f"# TYPE {name}_count gauge")
            lines.append(f"{name}_count{_format_labels(labels)} {count}")
            lines.append(f"{name}_sum{_format_labels(labels)} {total}")
            lines.append(f"{name}_avg{_format_labels(labels)} {avg}")
    return "\n".join(lines) + "\n"


def reset_metrics_for_tests() -> None:
    """
    函数作用：
        测试时清空进程内指标。
    输入参数：
        - 无
    输出参数：
        - 无
    """
    with _lock:
        _counters.clear()
        _histograms.clear()
