"""
模拟病患组件 - 时珍智训多智能体系统的垂直业务子模块

提供模拟中医临床问诊场景，让学习者通过与模拟患者对话训练辨证思维能力。
"""

from .engine import SimulatedPatientEngine
from .schemas import (
    SimulatedPatientRequest,
    SimulatedPatientResponse,
)
from .constants import PATIENT_STYLES
from .llm_provider import LLMProvider
from .default_providers import DefaultLLMProvider
from .data_provider import DefaultDataProvider

__all__ = [
    "SimulatedPatientEngine",
    "SimulatedPatientRequest",
    "SimulatedPatientResponse",
    "PATIENT_STYLES",
    "LLMProvider",
    "DefaultLLMProvider",
    "DefaultDataProvider",
]