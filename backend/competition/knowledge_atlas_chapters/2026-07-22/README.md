# 知识星球章节层级数据（2026-07-22）

这组文件为现有 83 本教材和 73,170 条 `source_chunks.jsonl` 切片补充：

```text
书籍 → 章节 → 小节 → 切片 → 知识点
```

原切片、知识点和题库文件均未修改。

## 文件

- `chapter_nodes.jsonl`：书籍、章节、小节节点及父子关系和顺序。
- `chunk_chapter_links.jsonl`：通过 `chunk_uid` 将每条切片关联到章节、小节。
- `chapter_hierarchy_report.json`：全量映射数量、识别方式和待复核统计。
- `chapter_hierarchy.py`：从原始 Markdown 与切片重新生成上述数据的脚本。

知识星球后端优先读取 `KNOWLEDGE_ATLAS_CHAPTER_ROOT`，其次读取公共资产目录
`03_pipeline_chunks/` 内的同名文件。旧公共资产包没有章节文件时，回退读取本目录。

## 当前统计

- 书籍：83
- 章节：1,282
- 原始小节节点：7,670
- 页面合并同章同名重复节点后的小节：5,186
- 映射切片：73,170 / 73,170
- 确定映射：73,034
- 待复核映射：136（保留稳定的 `UNRESOLVED_*` 标识）

`chunk_uid` 是与公共切片数据连接的唯一键，不得重新编号。
