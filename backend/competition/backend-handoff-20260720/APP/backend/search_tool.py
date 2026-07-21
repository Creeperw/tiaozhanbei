from exa_py import Exa
import logging
import threading

from APP.backend.config import EXA_API_KEY, EXA_NUM_RESULTS, EXA_CONTENT_CHAR_LIMIT

VIDEO_DOMAINS = ["youtube.com", "www.youtube.com", "bilibili.com", "www.bilibili.com"]

logger = logging.getLogger(__name__)
_exa = None
_exa_lock = threading.Lock()

def _get_exa_client():
    """首次搜索时再初始化 Exa 客户端，避免模块导入阶段做不必要工作。"""
    global _exa
    if _exa is not None:
        return _exa
    with _exa_lock:
        if _exa is None:
            _exa = Exa(EXA_API_KEY)
    return _exa

def perform_search(query: str, num_results=EXA_NUM_RESULTS):
    """
    执行 Exa 网络搜索
    """
    logger.info("执行 Exa 搜索: %s", query)
    try:
        # 使用 auto 改写搜索词，或者直接使用模型生成的 query
        result = _get_exa_client().search_and_contents(
            query,
            num_results=num_results,
            text=True,
            highlights=True
        )
        return result.results
    except Exception as e:
        logger.warning("Exa 搜索失败: %s", e)
        return []

def perform_video_search(query: str, num_results=3):
    """使用 Exa 检索适合动作演示、跟练或教学的视频页面。"""
    video_query = f"{query} 视频 演示 教学 跟练"
    logger.info("执行 Exa 视频搜索: %s", video_query)
    try:
        result = _get_exa_client().search_and_contents(
            video_query,
            num_results=num_results,
            text=True,
            highlights=True,
            include_domains=VIDEO_DOMAINS,
        )
        return result.results
    except TypeError:
        # 兼容旧版 exa_py：若不支持 include_domains，就用 site 约束兜底。
        fallback_query = f"{video_query} site:bilibili.com OR site:youtube.com"
        try:
            result = _get_exa_client().search_and_contents(
                fallback_query,
                num_results=num_results,
                text=True,
                highlights=True,
            )
            return result.results
        except Exception as e:
            logger.warning("Exa 视频搜索失败: %s", e)
            return []
    except Exception as e:
        logger.warning("Exa 视频搜索失败: %s", e)
        return []

def format_search_results(results):
    """
    将搜索结果格式化为 LLM 可读的 Context
    """
    if not results:
        return "未找到相关网络搜索结果。"
    
    context = "Web Search Results:\n\n"
    for res in results:
        context += f"Title: {res.title}\n"
        context += f"URL: {res.url}\n"
        context += f"Content: {res.text[:EXA_CONTENT_CHAR_LIMIT]}...\n\n" # 截断内容以节省 Context
    
    return context

def format_video_results(results):
    """将视频检索结果格式化为工具输出，保留 Video/Title/URL 字段便于解析。"""
    if not results:
        return "未找到相关视频结果。"

    context = "Video Search Results:\n\n"
    for idx, res in enumerate(results, 1):
        title = getattr(res, "title", "") or "视频结果"
        url = getattr(res, "url", "") or ""
        image = getattr(res, "image", "") or ""
        favicon = getattr(res, "favicon", "") or ""
        author = getattr(res, "author", "") or ""
        text = (getattr(res, "text", "") or "").strip()
        context += f"Video: {idx}\n"
        context += f"Title: {title}\n"
        context += f"URL: {url}\n"
        if image:
            context += f"Image: {image}\n"
        if favicon:
            context += f"Favicon: {favicon}\n"
        if author:
            context += f"Author: {author}\n"
        if text:
            context += f"Content: {text[:EXA_CONTENT_CHAR_LIMIT]}...\n"
        context += "\n"
    return context