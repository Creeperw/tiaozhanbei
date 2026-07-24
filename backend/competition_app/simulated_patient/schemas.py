"""
数据类定义 - 请求/响应对象
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Literal


@dataclass
class SimulatedPatientRequest:
    """模拟病患请求 - 唯一对外接口的输入"""
    
    user_id: str
    session_id: str
    action: Literal[
        "start", "dialogue", "help", "submit",
        "stats", "mistakes", "collections", "history", 
        "clear", "reset", "dialog_history", "history_detail"
    ]
    
    # dialogue 时必填
    user_input: str = ""
    
    # start 时可选（不传则自动筛选案例）
    case_id: Optional[str] = None
    
    # submit 时使用
    practice_scope: str = "full"
    diagnosis: Optional[Dict[str, str]] = None
    
    # help 时必填
    help_type: Optional[str] = None  # "question" | "interpretation"
    
    # history_detail 时必填
    history_id: Optional[str] = None
    
    # dialog_history 时可选
    limit: int = 100
    
    # 以下字段供生产环境使用，测试时由组件内部注入
    learner_context: Optional[Dict] = None
    evidence_pack: Optional[Dict] = None
    diagnosis_result: Optional[Dict] = None


@dataclass
class SimulatedPatientResponse:
    """模拟病患响应 - 唯一对外接口的输出"""
    
    session_id: str
    action: str
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    is_complete: bool = False
    turn_count: int = 0
    help_available: bool = False
    writeback_intents: List[Dict] = field(default_factory=list)