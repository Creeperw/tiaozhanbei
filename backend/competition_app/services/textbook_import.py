from __future__ import annotations



import asyncio

import base64

import hashlib

import json

import os

import re

import shutil

import statistics

import subprocess

import sys

import tempfile

import time

from collections.abc import Callable

from datetime import datetime, timezone

from difflib import SequenceMatcher

from io import BytesIO

from pathlib import Path

from typing import Any

from uuid import uuid4



import httpx

from PIL import Image, ImageDraw

from pypdf import PdfReader, PdfWriter



from competition_app.tools.textbook_chunking import (

    build_chunk_index,

    chunk_book_by_toc,

    match_chunks,

)





TOC_EXTRACTION_FAILED = "TEXTBOOK_TOC_EXTRACTION_FAILED"

MAX_MARKITDOWN_SIZE = 200 * 1024 * 1024





class TextbookImportError(RuntimeError):

    code = "TEXTBOOK_IMPORT_FAILED"





class TextbookTocNotFound(TextbookImportError):

    code = TOC_EXTRACTION_FAILED





class TextbookTooLargeError(TextbookImportError):

    code = "TEXTBOOK_TOO_LARGE"





def _json_object(value: str) -> dict[str, Any]:

    text = str(value or "").strip()

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)

    try:

        payload = json.loads(text)

    except json.JSONDecodeError:

        start, end = text.find("{"), text.rfind("}")

        if start < 0 or end <= start:

            raise TextbookImportError("多模态模型没有返回有效 JSON")

        payload = json.loads(text[start : end + 1])

    if not isinstance(payload, dict):

        raise TextbookImportError("多模态模型返回的数据不是 JSON 对象")

    return payload





def _safe_owner(value: str) -> str:

    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "anonymous"))[:96]





def _clean_title(value: str) -> str:

    title = Path(str(value or "教材")).stem

    title = re.sub(r"^(?:马工程|教材)[-_\s]*", "", title)

    title = title.replace("《", "").replace("》", "").strip()

    return title or "用户教材"





def _normalized(value: str) -> str:

    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())





class TextbookImportService:

    """User-scoped textbook ingestion.



    TOC location and structure are decided only by the configured multimodal

    model. MinerU is the sole full-book content parser.

    """



    def __init__(

        self,

        *,

        runtime_root: Path,

        chat_base_url: str,

        chat_model: str,

        chat_api_key: str | None,

        mineru_token: str | None,

        mineru_pipeline_root: Path,

        timeout_seconds: float = 180.0,

        embedding_model: Any | None = None,

        vector_store_root: Path | None = None,

        knowledge_resolver: Any | None = None,

        vision_base_url: str = "",

        vision_model: str = "",

        vision_api_key: str = "",

        treekg_root: Path | None = None,

        treekg_python: str = "",

        treekg_api_key: str = "",

        treekg_api_base: str = "https://api.deepseek.com",

        treekg_model_name: str = "deepseek-v4-flash",

    ) -> None:

        self.runtime_root = (runtime_root / "textbook_uploads").resolve()

        self.chat_base_url = str(chat_base_url).rstrip("/")

        self.chat_model = str(chat_model).strip()

        self.chat_api_key = str(chat_api_key or "").strip()

        self.mineru_token = str(mineru_token or "").strip()

        self.mineru_pipeline_root = mineru_pipeline_root.resolve()

        self.timeout_seconds = max(float(timeout_seconds), 60.0)

        self.embedding_model = embedding_model

        self.vector_store_root = vector_store_root

        self.knowledge_resolver = knowledge_resolver

        self.vision_base_url = str(vision_base_url or "").rstrip("/")

        self.vision_model = str(vision_model or "").strip()

        self.vision_api_key = str(vision_api_key or "").strip()

        self.treekg_root = Path(treekg_root).resolve() if treekg_root else None

        self.treekg_python = str(treekg_python or "").strip()

        self.treekg_api_key = str(treekg_api_key or "").strip()

        self.treekg_api_base = str(treekg_api_base or "https://api.deepseek.com").strip()

        self.treekg_model_name = str(treekg_model_name or "deepseek-v4-flash").strip()



    def categories(self) -> list[str]:

        values = {"中医药"}

        if self.runtime_root.is_dir():

            for manifest in self.runtime_root.glob("*/*/manifest.json"):

                try:

                    item = json.loads(manifest.read_text(encoding="utf-8"))

                    category = str(item.get("category") or "").strip()

                    if category:

                        values.add(category)

                except (OSError, json.JSONDecodeError):

                    continue

        return sorted(values, key=lambda item: (item != "中医药", item))



    async def import_pdf(

        self,

        *,

        owner_id: str,

        filename: str,

        content: bytes,

        title: str = "",

        description: str = "",

        category: str = "中医药",

        new_category: str = "",

        cover_content: bytes | None = None,

        cover_media_type: str = "",

        match_local: bool = False,

        allow_large: bool = False,

        progress: Callable[[str, str], None] | None = None,

    ) -> dict[str, Any]:

        if not self.chat_api_key or not self.chat_model:

            raise TextbookImportError("多模态模型未配置")

        if not self.mineru_token:

            raise TextbookImportError("MinerU 服务端密钥未配置")

        if Path(filename).suffix.lower() != ".pdf":

            raise TextbookImportError("教材上传目前仅支持 PDF")

        if not content:

            raise TextbookImportError("教材文件为空")

        if len(content) > 512 * 1024 * 1024:

            raise TextbookImportError("教材 PDF 不能超过 512 MB")

        if len(content) > MAX_MARKITDOWN_SIZE and not allow_large:

            raise TextbookTooLargeError("当前教材超过大小限制（200MB），解析质量可能下降")



        owner = _safe_owner(owner_id)

        digest = hashlib.sha256(content).hexdigest()

        owner_root = self.runtime_root / owner

        owner_root.mkdir(parents=True, exist_ok=True)

        # Failed imports are deliberately retained for diagnosis. Reuse the

        # newest unfinished directory for the same PDF so a CDN-only failure

        # can resume MinerU instead of submitting the book again.

        book_dir = next(

            (

                candidate

                for candidate in sorted(owner_root.glob("UTB_*/"), key=lambda p: p.stat().st_mtime, reverse=True)

                if (candidate / "textbook.pdf").is_file()

                and hashlib.sha256((candidate / "textbook.pdf").read_bytes()).hexdigest() == digest

                and not (candidate / "manifest.json").is_file()

            ),

            None,

        )

        if book_dir is None:

            book_id = f"UTB_{digest[:16]}_{uuid4().hex[:6]}"

            book_dir = owner_root / book_id

            book_dir.mkdir(parents=True, exist_ok=False)

        else:

            book_id = book_dir.name

        pdf_path = book_dir / "textbook.pdf"

        if not pdf_path.is_file():

            pdf_path.write_bytes(content)



        try:

            document = PdfReader(str(pdf_path))

            if len(document.pages) < 1:

                raise TextbookImportError("PDF 没有可读取页面")

            self._report(progress, "toc", "正在定位并识别目录…")

            locator, extracted = await self._recognize_toc(

                pdf_path, document, digest

            )

            toc_pages = sorted({

                int(page) for page in locator.get("toc_pdf_pages", [])

                if str(page).isdigit() and 1 <= int(page) <= len(document.pages)

            })

            if not locator.get("has_toc") or not toc_pages:

                raise TextbookTocNotFound("未能从教材中提取目录")

            chapters = extracted.get("chapters")

            if not isinstance(chapters, list) or not chapters:

                raise TextbookTocNotFound("未能从教材中提取目录")

            generated_title = str(extracted.get("book_title") or "").strip()

            generated_summary = str(extracted.get("summary") or "").strip()



            text_layer = self._has_text_layer(pdf_path, document)

            if text_layer:

                content_parser = "markitdown"

                self._report(progress, "extract", "正在解析正文（markitdown，本地处理）…")

                markdown_path = await asyncio.to_thread(

                    self._run_markitdown, pdf_path, book_dir / "markitdown"

                )

                page_text = self._page_text_pypdf(pdf_path, document)

            else:

                if len(content) > MAX_MARKITDOWN_SIZE:

                    raise TextbookImportError("当前教材为扫描版且超过 200MB，markitdown 无法提取文字")

                content_parser = "mineru"

                self._report(progress, "extract", "正在解析正文（MinerU，云端 OCR）…")

                mineru_dir = book_dir / "mineru"

                mineru_markdown, page_text = await asyncio.to_thread(

                    self._run_mineru, pdf_path, mineru_dir

                )

            self._report(progress, "toc", "正在映射目录页码…")

            mapped_toc = self._map_toc_pages(

                chapters,

                page_text,

                locator.get("page_number_anchors") or [],

                document_page_count=self._page_count(pdf_path),

                toc_last_page=max(toc_pages),

            )



            final_title = str(title or generated_title or _clean_title(filename)).strip()

            final_description = str(description or generated_summary).strip()

            if not final_description:

                final_description = f"《{final_title}》按教材目录系统组织章节内容，支持电子阅读、目录跳转与学习批注。"

            final_category = str(new_category or category or "中医药").strip() or "中医药"

            cover_name = self._save_cover(

                pdf_path, book_dir, cover_content, cover_media_type

            )



            chunk_stats: dict[str, Any] = {"match_local": match_local}

            if match_local:

                self._report(progress, "chunk", "正在按目录章节切片…")

                sections, chunks = await asyncio.to_thread(

                    chunk_book_by_toc,

                    page_text,

                    mapped_toc,

                    book_title=final_title,

                    page_count=self._page_count(pdf_path),

                    marker_prefix=book_id,

                )

                if chunks and self.embedding_model is not None:

                    self._report(progress, "embed", "正在向量化切片…")

                    await build_chunk_index(

                        chunks, self.embedding_model, book_dir / "chunks"

                    )

                    self._report(progress, "match", "正在匹配题库与知识点…")

                    stats = await match_chunks(

                        chunks,

                        sections,

                        embedding_model=self.embedding_model,

                        vector_store_root=(

                            self.vector_store_root if self.vector_store_root is not None else Path("")

                        ),

                        kp_resolver=self.knowledge_resolver,

                        out_path=book_dir / "chunk_matches.jsonl",

                    )

                    chunk_stats.update({

                        "chunk_count": stats["chunk_count"],

                        "chunk_matched_kp": stats["matched_kp"],

                        "chunk_matched_question": stats["matched_question"],

                        "chunk_index_relative_path": "chunks/index.faiss",

                        "chunk_matches_relative_path": "chunk_matches.jsonl",

                    })

                if self.treekg_root is not None:

                    self._report(progress, "graph", "正在构建知识图谱…")

                    kg = await asyncio.to_thread(

                        self._run_treekg, book_dir, final_title, book_id, progress

                    )

                    chunk_stats.update({

                        "kg_status": kg.get("status", "skipped"),

                        "kg_graph_id": kg.get("graph_id"),

                        "kg_manifest_relative_path": kg.get("manifest_relative_path"),

                        "kg_node_count": kg.get("node_count", 0),

                        "kg_edge_count": kg.get("edge_count", 0),

                        "kg_message": kg.get("message"),

                    })

                else:

                    chunk_stats.update({"chunk_count": len(chunks), "chunk_index_relative_path": None})



            manifest = {

                "book_id": book_id,

                "title": final_title,

                "aliases": [final_title],

                "edition": "用户上传",

                "category": final_category,

                "description": final_description[:500],

                "relative_path": "textbook.pdf",

                "cover_relative_path": cover_name,

                "size_bytes": len(content),

                "sha256": digest,

                "page_count": self._page_count(pdf_path),

                "toc": {"chapters": mapped_toc, "source_pdf_pages": toc_pages},

                "toc_status": "extracted",

                "content_parser": content_parser,

                "toc_parser": "multimodal_llm",

                "text_parser_source": "pypdf" if text_layer else "mineru",

                "owner_id": owner_id,

                "origin": "user_upload",

                "created_at": datetime.now(timezone.utc).isoformat(),

                **chunk_stats,

            }

            if content_parser == "mineru":

                manifest["mineru_markdown_relative_path"] = str(

                    mineru_markdown.relative_to(book_dir)

                ).replace("\\", "/")

            else:

                manifest["markitdown_markdown_relative_path"] = str(

                    markdown_path.relative_to(book_dir)

                ).replace("\\", "/")

            (book_dir / "manifest.json").write_text(

                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"

            )

            (book_dir / "toc.json").write_text(

                json.dumps(manifest["toc"], ensure_ascii=False, indent=2), encoding="utf-8"

            )

            self._report(progress, "done", "教材处理完成")

            return manifest

        except Exception:

            # Keep failed runs for diagnosis, but never expose model credentials.

            raise



    @staticmethod

    def _report(progress: Callable[[str, str], None] | None, step: str, label: str) -> None:

        if progress:

            progress(step, label)



    def _has_text_layer(self, pdf_path: Path, document: Any) -> bool:

        total = 0

        for index in range(min(8, len(document.pages))):

            try:

                total += len((document.pages[index].extract_text() or "").strip())

            except Exception:

                continue

        return total >= 40



    def _page_text_pypdf(self, pdf_path: Path, document: Any) -> dict[int, str]:

        output: dict[int, str] = {}

        for index in range(len(document.pages)):

            try:

                text = document.pages[index].extract_text() or ""

            except Exception:

                text = ""

            output[index + 1] = re.sub(r"[ \t\u3000]+", " ", text).strip()

        return output



    def _run_treekg(self, book_dir: Path, title: str, book_id: str,
                    progress: Callable[[str, str], None] | None = None) -> dict[str, Any]:

        script = self.treekg_root / "integration" / "build_graph.py"

        if script is None or not script.is_file():

            return {"status": "skipped", "message": "知识图谱流水线未部署"}

        chunks = book_dir / "chunks" / "metadata.jsonl"

        if not chunks.is_file():

            return {"status": "skipped", "message": "无切片数据"}

        if not self.treekg_api_key:

            return {"status": "skipped", "message": "未配置 TREEKG_API_KEY"}

        job_root = book_dir / "treekg"

        job_root.mkdir(parents=True, exist_ok=True)

        shutil.copy2(chunks, job_root / "chunks.jsonl")

        request = {

            "schema_version": "1.0.0",

            "job_id": f"KG_{book_id}",

            "user_id": "user_upload",

            "book": {"book_id": book_id, "title": title},

            "source": {"chunks_path": "chunks.jsonl", "format": "textbook_pipeline_jsonl"},

        }

        (job_root / "request.json").write_text(

            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"

        )

        python = self.treekg_python or sys.executable

        env = os.environ.copy()

        env.update({

            "TREEKG_API_KEY": self.treekg_api_key,

            "TREEKG_API_BASE": self.treekg_api_base,

            "TREEKG_MODEL_NAME": self.treekg_model_name,

            "PYTHONUTF8": "1",

            "PYTHONIOENCODING": "utf-8",

        })

        try:

            process = subprocess.Popen(

                [python, str(script), "--request", str(job_root / "request.json"),

                 "--output", str(job_root)],

                cwd=str(self.treekg_root),

                env=env,

                stdout=subprocess.PIPE,

                stderr=subprocess.STDOUT,

                text=True,

                encoding="utf-8",

                errors="replace",

                bufsize=1,

            )

        except FileNotFoundError as exc:

            return {"status": "failed", "message": f"知识图谱 python 不存在: {python}"}

        assert process.stdout is not None

        for line in process.stdout:

            message = line.rstrip()

            if not message:

                continue

            try:

                event = json.loads(message)

                stage = str(event.get("stage") or "")

                status = str(event.get("status") or "")

                if stage in {"normalize", "explicit", "hidden", "publish", "done", "failed"} and status:

                    text = str(event.get("message") or "")[:80]

                    self._report(progress, "graph", f"知识图谱：{text or stage}")

            except (ValueError, TypeError):

                continue

        return_code = process.wait()

        if return_code != 0:

            detail = (job_root / "manifest.json")

            message = "知识图谱构建失败"

            if detail.is_file():

                try:

                    payload = json.loads(detail.read_text(encoding="utf-8"))

                    message = str((payload.get("error") or {}).get("message") or message)

                except (OSError, json.JSONDecodeError):

                    pass

            return {"status": "failed", "message": message[:300]}

        manifest_path = job_root / "manifest.json"

        if manifest_path.is_file():

            try:

                payload = json.loads(manifest_path.read_text(encoding="utf-8"))

            except (OSError, json.JSONDecodeError):

                payload = {}

            counts = payload.get("counts") or {}

            return {

                "status": payload.get("status", "ready"),

                "graph_id": payload.get("graph_id"),

                "manifest_relative_path": "treekg/manifest.json",

                "node_count": int(counts.get("nodes", 0) or 0),

                "edge_count": int(counts.get("edges", 0) or 0),

            }

        return {"status": "ready", "manifest_relative_path": "treekg/manifest.json"}



    def _run_markitdown(self, pdf_path: Path, output_dir: Path) -> Path:

        from markitdown import MarkItDown

        output_dir.mkdir(parents=True, exist_ok=True)

        result = MarkItDown().convert(str(pdf_path))

        content = str(result.text_content or "").strip()

        if not content:

            raise TextbookImportError("markitdown 未能从 PDF 提取文本（可能是扫描版）")

        target = output_dir / "textbook_full_clean.md"

        target.write_text(content, encoding="utf-8")

        return target



    async def _recognize_toc(

        self, pdf_path: Path, document: PdfReader, digest: str

    ) -> tuple[dict[str, Any], dict[str, Any]]:

        cache_dir = self.runtime_root / "_toc_cache"

        cache_path = cache_dir / f"{digest}.json"

        if cache_path.is_file():

            try:

                payload = json.loads(cache_path.read_text(encoding="utf-8"))

                locator = payload.get("locator")

                extracted = payload.get("extracted")

                if isinstance(locator, dict) and isinstance(extracted, dict):

                    return locator, extracted

            except (OSError, json.JSONDecodeError):

                pass

        locator = await self._locate_toc(pdf_path, document)

        toc_pages = sorted({

            int(page) for page in locator.get("toc_pdf_pages", [])

            if str(page).isdigit() and 1 <= int(page) <= len(document.pages)

        })

        if not locator.get("has_toc") or not toc_pages:

            raise TextbookTocNotFound("未能从教材中提取目录")

        extracted = await self._extract_toc(pdf_path, toc_pages)

        if not isinstance(extracted.get("chapters"), list) or not extracted["chapters"]:

            raise TextbookTocNotFound("未能从教材中提取目录")

        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_path.write_text(

            json.dumps({"locator": locator, "extracted": extracted}, ensure_ascii=False, indent=2),

            encoding="utf-8",

        )

        return locator, extracted



    def _candidate_pages(self, document: PdfReader) -> list[int]:

        limit = min(len(document.pages), 72)

        hits: list[int] = []

        for index in range(limit):

            try:

                text = str(document.pages[index].extract_text() or "")[:12000]

            except Exception:

                text = ""

            if "目录" in text or len(re.findall(r"[\.·…]{3,}\s*\d+", text)) >= 3:

                hits.append(index + 1)

        if hits:

            pages: set[int] = set()

            for page in hits:

                pages.update(range(max(1, page - 2), min(limit, page + 8) + 1))

            return sorted(pages)[:36]

        return list(range(1, min(len(document.pages), 36) + 1))



    def _render_page(self, pdf_path: Path, page_number: int, scale: float) -> Image.Image:

        self.runtime_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=self.runtime_root) as temp_dir:

            prefix = Path(temp_dir) / "page"

            dpi = max(48, min(180, int(72 * scale)))

            completed = subprocess.run(

                [

                    shutil.which("pdftoppm.exe") or shutil.which("pdftoppm") or "pdftoppm",

                    "-f", str(page_number), "-l", str(page_number),

                    "-singlefile", "-png", "-r", str(dpi), str(pdf_path), str(prefix),

                ],

                capture_output=True,

                timeout=180,

                check=False,

            )

            output = prefix.with_suffix(".png")

            if completed.returncode != 0 or not output.is_file():

                raise TextbookImportError("PDF 页面渲染失败")

            with Image.open(output) as image:

                return image.convert("RGB").copy()



    def _contact_sheets(self, pdf_path: Path, pages: list[int]) -> list[tuple[list[int], bytes]]:

        sheets: list[tuple[list[int], bytes]] = []

        for offset in range(0, len(pages), 9):

            group = pages[offset : offset + 9]

            sheet = Image.new("RGB", (1080, 1440), "white")

            draw = ImageDraw.Draw(sheet)

            for slot, page_number in enumerate(group):

                page = self._render_page(pdf_path, page_number, 0.5)

                page.thumbnail((330, 410))

                x = 20 + (slot % 3) * 350

                y = 42 + (slot // 3) * 460

                sheet.paste(page, (x + (330 - page.width) // 2, y))

                draw.text((x + 6, y - 26), f"PDF PAGE {page_number}", fill="#075b40")

            buffer = BytesIO()

            sheet.save(buffer, format="JPEG", quality=78, optimize=True)

            sheets.append((group, buffer.getvalue()))

        return sheets



    @staticmethod

    def _image_part(content: bytes) -> dict[str, Any]:

        encoded = base64.b64encode(content).decode("ascii")

        return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}



    async def _locate_toc(self, pdf_path: Path, document: PdfReader) -> dict[str, Any]:

        candidates = self._candidate_pages(document)

        content: list[dict[str, Any]] = [{

            "type": "text",

            "text": (

                "你是教材目录页定位器。下面是 PDF 前部候选页面的缩略图拼图，每张小图顶部标有真实 PDF 页码。"

                "只能依据图像判断目录，不得把序言、版权页、正文或索引误判为目录。"

                "返回严格 JSON：{\"has_toc\":true/false,\"toc_pdf_pages\":[真实PDF页码],"

                "\"page_number_anchors\":[{\"pdf_page\":真实PDF页码,\"printed_page\":页面印刷页码}],"

                "\"reason\":\"简短原因\"}。若目录不存在或无法确认，has_toc 必须为 false。"

            ),

        }]

        for group, image in self._contact_sheets(pdf_path, candidates):

            content.append({"type": "text", "text": f"候选 PDF 页：{group}"})

            content.append(self._image_part(image))

        return await self._vision_json(content, max_tokens=1600)



    async def _extract_toc(self, pdf_path: Path, toc_pages: list[int]) -> dict[str, Any]:

        content: list[dict[str, Any]] = [{

            "type": "text",

            "text": (

                "你是教材目录结构提取器。以下均为上一阶段确认过的目录页。请逐字识别目录层级和目录中印刷的页码。"

                "不得臆造目录之外的章节。返回严格 JSON："

                "{\"book_title\":\"可识别则填写\",\"summary\":\"不超过80字的一行教材介绍\","

                "\"chapters\":[{\"title\":\"章名\",\"printed_page\":数字或null,"

                "\"sections\":[{\"title\":\"节名\",\"printed_page\":数字或null}]}]}。"

                "只有篇/编而没有章时，把篇/编作为 chapter；章下无明确小节时 sections 返回空数组。"

            ),

        }]

        for page_number in toc_pages:

            image = self._render_page(pdf_path, page_number, 1.45)

            buffer = BytesIO()

            image.save(buffer, format="JPEG", quality=88, optimize=True)

            content.append({"type": "text", "text": f"真实 PDF 页码：{page_number}"})

            content.append(self._image_part(buffer.getvalue()))

        return await self._vision_json(content, max_tokens=8000)



    async def _vision_json(self, user_content: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:

        # 目录识别必须看图：优先使用专用视觉模型，未配置时回退聊天模型。

        base_url = self.vision_base_url or self.chat_base_url

        model = self.vision_model or self.chat_model

        api_key = self.vision_api_key or self.chat_api_key

        request = {

            "model": model,

            "messages": [

                {"role": "system", "content": "你只负责从教材页面图像识别目录并输出有效 JSON，不输出 Markdown。"},

                {"role": "user", "content": user_content},

            ],

            "response_format": {"type": "json_object"},

            "temperature": 0,

            "max_tokens": max_tokens,

        }

        timeout = httpx.Timeout(max(self.timeout_seconds, 600.0), connect=30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:

            response = await client.post(

                f"{base_url}/chat/completions",

                headers={"Authorization": f"Bearer {api_key}"},

                json=request,

            )

            if response.status_code == 400 and "response_format" in response.text:

                request.pop("response_format", None)

                response = await client.post(

                    f"{self.chat_base_url}/chat/completions",

                    headers={"Authorization": f"Bearer {self.chat_api_key}"},

                    json=request,

                )

            try:

                response.raise_for_status()

            except httpx.HTTPStatusError as exc:

                raise TextbookImportError(f"多模态模型调用失败（HTTP {response.status_code}）") from exc

            body = response.json()

            result = body.get("choices", [{}])[0].get("message", {}).get("content", "")

            return _json_object(result)



    def _run_mineru(self, pdf_path: Path, output_dir: Path) -> tuple[Path, dict[int, str]]:

        script = self.mineru_pipeline_root / "parse_question_pdf.py"

        config = self.mineru_pipeline_root / "pipeline_config.json"

        if not script.is_file() or not config.is_file():

            raise TextbookImportError("MinerU 处理管线不完整")

        output_dir.mkdir(parents=True, exist_ok=True)

        pdf_inputs = self._split_for_mineru(pdf_path, output_dir)

        env = os.environ.copy()

        env["MINERU_TOKEN"] = self.mineru_token

        command = [

            sys.executable,

            str(script),

            "--config", str(config),

            "--output-dir", str(output_dir),

        ]

        for item in pdf_inputs:

            command.extend(["--pdf", str(item)])

        completed = None

        for attempt in range(3):

            completed = subprocess.run(

                command,

                cwd=self.mineru_pipeline_root,

                env=env,

                capture_output=True,

                text=True,

                encoding="utf-8",

                errors="replace",

                timeout=24 * 60 * 60,

                check=False,

            )

            if completed.returncode == 0:

                break

            detail = (completed.stderr or completed.stdout or "").lower()

            if "download failed after retries" not in detail or attempt >= 2:

                break

            time.sleep(4 * (attempt + 1))

        assert completed is not None

        if completed.returncode != 0:

            detail = (completed.stderr or completed.stdout or "MinerU 解析失败").strip()

            if "download failed after retries" in detail.lower():

                raise TextbookImportError(

                    "MinerU 已完成解析，但结果文件下载失败；请检查到 cdn-mineru.openxlab.org.cn 的网络后重试"

                )

            raise TextbookImportError(detail[-1500:])

        markdown_files = sorted(output_dir.rglob("*_clean.md")) or sorted(output_dir.rglob("*.md"))

        if not markdown_files:

            raise TextbookImportError("MinerU 未生成教材 Markdown")

        combined = output_dir / "textbook_full_clean.md"

        combined.write_text(

            "\n\n".join(path.read_text(encoding="utf-8-sig") for path in markdown_files),

            encoding="utf-8",

        )

        page_text = self._mineru_page_text(output_dir)

        return combined, page_text



    def _split_for_mineru(self, pdf_path: Path, output_dir: Path) -> list[Path]:

        reader = PdfReader(str(pdf_path))

        if len(reader.pages) <= 180:

            return [pdf_path]

        chunk_root = output_dir / "input_chunks"

        chunk_root.mkdir(parents=True, exist_ok=True)

        chunks: list[Path] = []

        for start in range(0, len(reader.pages), 180):

            end = min(len(reader.pages), start + 180)

            target = chunk_root / f"textbook_pages_{start + 1}-{end}.pdf"

            writer = PdfWriter()

            for page in reader.pages[start:end]:

                writer.add_page(page)

            with target.open("wb") as stream:

                writer.write(stream)

            chunks.append(target)

        return chunks



    def _mineru_page_text(self, output_dir: Path) -> dict[int, str]:

        pages: dict[int, list[str]] = {}

        for path in output_dir.rglob("*content_list*.json"):

            try:

                payload = json.loads(path.read_text(encoding="utf-8-sig"))

            except (OSError, json.JSONDecodeError):

                continue

            rows = payload if isinstance(payload, list) else payload.get("content_list", [])

            offset_match = re.search(r"pages_(\d+)-\d+", str(path))

            chunk_offset = int(offset_match.group(1)) - 1 if offset_match else 0

            for row in rows if isinstance(rows, list) else []:

                if not isinstance(row, dict):

                    continue

                index = row.get("page_idx", row.get("page_index", row.get("page_no")))

                try:

                    page_number = int(index) + (0 if row.get("page_no") is not None else 1) + chunk_offset

                except (TypeError, ValueError):

                    continue

                text_parts = [

                    row.get("text"), row.get("content"), row.get("img_caption"),

                    row.get("table_body"), row.get("title"),

                ]

                value = " ".join(str(item) for item in text_parts if isinstance(item, (str, int, float)))

                if value:

                    pages.setdefault(page_number, []).append(value)

        return {page: "\n".join(parts) for page, parts in pages.items()}



    def _map_toc_pages(

        self,

        chapters: list[Any],

        page_text: dict[int, str],

        model_anchors: list[Any],

        *,

        document_page_count: int,

        toc_last_page: int,

    ) -> list[dict[str, Any]]:

        entries: list[tuple[dict[str, Any], str]] = []

        mapped: list[dict[str, Any]] = []

        for chapter_index, raw_chapter in enumerate(chapters, 1):

            if not isinstance(raw_chapter, dict):

                continue

            chapter = {

                "id": f"chapter-{chapter_index}",

                "title": str(raw_chapter.get("title") or f"第{chapter_index}章").strip(),

                "printed_page": self._integer_or_none(raw_chapter.get("printed_page")),

                "sections": [],

            }

            entries.append((chapter, chapter["title"]))

            for section_index, raw_section in enumerate(raw_chapter.get("sections") or [], 1):

                if not isinstance(raw_section, dict):

                    continue

                section = {

                    "id": f"chapter-{chapter_index}-section-{section_index}",

                    "title": str(raw_section.get("title") or f"第{section_index}节").strip(),

                    "printed_page": self._integer_or_none(raw_section.get("printed_page")),

                }

                chapter["sections"].append(section)

                entries.append((section, section["title"]))

            mapped.append(chapter)



        offsets: list[int] = []

        for anchor in model_anchors:

            if not isinstance(anchor, dict):

                continue

            pdf_page = self._integer_or_none(anchor.get("pdf_page"))

            printed_page = self._integer_or_none(anchor.get("printed_page"))

            if pdf_page and printed_page is not None:

                offsets.append(pdf_page - printed_page)



        for target, title in entries:

            title_key = _normalized(title)

            best_page, best_score = None, 0.0

            if title_key:

                for page, text in page_text.items():

                    if page <= toc_last_page:

                        continue

                    text_key = _normalized(text)

                    if not text_key:

                        continue

                    score = 1.0 if title_key in text_key else SequenceMatcher(None, title_key, text_key[: max(120, len(title_key) * 8)]).ratio()

                    if score > best_score:

                        best_page, best_score = page, score

            if best_page is not None and best_score >= 0.58:

                target["pdf_page"] = best_page

                target["page_match"] = "mineru_title"

                printed = target.get("printed_page")

                if isinstance(printed, int):

                    offsets.append(best_page - printed)



        page_offset = int(round(statistics.median(offsets))) if offsets else toc_last_page

        for target, _ in entries:

            if "pdf_page" not in target:

                printed = target.get("printed_page")

                if isinstance(printed, int):

                    target["pdf_page"] = min(document_page_count, max(1, printed + page_offset))

                    target["page_match"] = "page_offset"

                else:

                    target["pdf_page"] = min(document_page_count, max(1, toc_last_page + 1))

                    target["page_match"] = "toc_fallback"

        return mapped



    @staticmethod

    def _integer_or_none(value: Any) -> int | None:

        try:

            return int(value) if value is not None and str(value).strip() else None

        except (TypeError, ValueError):

            return None



    @staticmethod

    def _page_count(pdf_path: Path) -> int:

        return len(PdfReader(str(pdf_path)).pages)



    def _save_cover(

        self,

        pdf_path: Path,

        book_dir: Path,

        custom_cover: bytes | None,

        media_type: str,

    ) -> str:

        if custom_cover:

            if len(custom_cover) > 10 * 1024 * 1024:

                raise TextbookImportError("自定义封面不能超过 10 MB")

            try:

                with Image.open(BytesIO(custom_cover)) as uploaded:

                    uploaded.verify()

            except Exception as exc:

                raise TextbookImportError("自定义封面不是有效图片") from exc

            suffix = {"image/png": ".png", "image/webp": ".webp"}.get(media_type, ".jpg")

            target = book_dir / f"cover{suffix}"

            target.write_bytes(custom_cover)

            return target.name

        image = self._render_page(pdf_path, 1, 1.15)

        target = book_dir / "cover.jpg"

        image.save(target, format="JPEG", quality=88, optimize=True)

        return target.name

