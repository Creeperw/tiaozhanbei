# 单文件 shim：把根目录下的 intent_reply_template.py 暴露为 APP.intent_reply_template。
# 真源仍在项目根目录 ./intent_reply_template.py（保留原有目录布局，编辑那里即可）。
# 这样 server 端的 `from APP.intent_reply_template import ...` 写法在本地直接可用。
import importlib.util
import pathlib
import sys

_THIS = pathlib.Path(__file__).resolve()
_SOURCE = _THIS.parent.parent / "intent_reply_template.py"

_spec = importlib.util.spec_from_file_location("APP.intent_reply_template", _SOURCE)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load intent_reply_template from {_SOURCE}")

_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
sys.modules[__name__] = _module
