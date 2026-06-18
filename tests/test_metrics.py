from __future__ import annotations

from services.metrics import inc_counter, observe, render_prometheus, reset_metrics_for_tests


def setup_function():
    reset_metrics_for_tests()


def test_metrics_render_prometheus_text():
    inc_counter("legal_test_total", {"status": "ok"})
    observe("legal_latency_ms", 10, {"route": "fast"})
    observe("legal_latency_ms", 30, {"route": "fast"})

    text = render_prometheus()

    assert 'legal_test_total{status="ok"} 1.0' in text
    assert 'legal_latency_ms_count{route="fast"} 2' in text
    assert 'legal_latency_ms_avg{route="fast"} 20.0' in text
