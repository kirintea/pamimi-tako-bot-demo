# -*- coding: utf-8 -*-

from __future__ import annotations

import typing

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from core.config.schemas import OTelConfig


def _resource_attributes(config: OTelConfig) -> dict[str, str]:
    """组装 Resource 属性

    抽为独立函数便于单测（tests/test_message_channel.py），不触发全局 TracerProvider。
    channel 为进程级渠道标识（D11）：一个进程一个渠道，飞书/微信接入 = 独立部署 + OTEL_CHANNEL。
    """
    return {
        "service.name": config.service_name,
        "service.version": config.service_version,
        "deployment.environment": config.environment,
        "channel": config.channel,
    }


class TracingSetup:
    """OTel 追踪初始化 — 供 AgentScope TracingMiddleware 使用

    必须在 Agent 创建之前调用，之后 TracingMiddleware 自动生效。
    """

    @staticmethod
    def init(config: OTelConfig) -> TracerProvider:
        """初始化 OpenTelemetry SDK

        Args:
            config: OTel 配置

        Returns:
            配置好的 TracerProvider
        """
        resource = Resource.create(_resource_attributes(config))

        exporter = OTLPSpanExporter(
            endpoint=config.endpoint,
            headers=config.headers or None,
        )

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))

        trace.set_tracer_provider(provider)
        return provider

    @staticmethod
    def shutdown() -> None:
        """关闭追踪，刷新剩余数据"""
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            typing.cast(TracerProvider, provider).shutdown()
            # provider.shutdown()
