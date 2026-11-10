"""第 3 步：输入文档解析器（Preprocessor）。

将两份 Markdown 架构文档转换为结构化 JSON，供 Code Agent 作为输入：

* Architecture_Documentation.md → 文档结构（章节/表格/代码块/列表）+ 语义抽取
  （追溯矩阵、组件、技术栈、风险、QA 映射、交付物清单）
* Architecture_View.md         → 全部 PlantUML 图的结构化描述
  （元素、关系、活动流、备注）

同时输出确定性的"缺失/矛盾"检查结果（analysis.gaps），
帮助 Agent 在生成代码之前发现设计文档自身的问题。

设计原则：纯确定性解析（不用 LLM），结果可复现、可测试。

用法:
    python -m codeagent.preprocessor <架构文档.md> <架构视图.md> -o out.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class CodeBlock:
    """Markdown 围栏代码块。"""
    language: str
    content: str


@dataclass
class Table:
    """Markdown 管道表格。"""
    headers: list[str]
    rows: list[list[str]]


@dataclass
class Section:
    """文档中的一个标题及其正文。"""
    id: str                        # 如 "A"、"B"；无编号时为 ""
    title: str                     # 标题文本（不含编号）
    level: int                     # 1-6
    content_md: str                # 该节原始 Markdown 正文
    tables: list[Table] = field(default_factory=list)
    code_blocks: list[CodeBlock] = field(default_factory=list)
    lists: list[str] = field(default_factory=list)


@dataclass
class PlantUmlDiagram:
    """一张 PlantUML 图的结构化描述。"""
    name: str                                    # @startuml 后的名字
    view: str                                    # 所属视图（如 "LogicView"）
    kind: str                                    # 图类型（如 "Use Case Diagram"）
    raw: str                                     # 原始 PlantUML 源码
    elements: dict = field(default_factory=dict)  # actors/usecases/classes/... 分类
    relations: list = field(default_factory=list)  # {from, to, arrow, label}
    notes: list = field(default_factory=list)
    flow: list = field(default_factory=list)      # 活动图流程/无法归类的原始行


# ---------------------------------------------------------------------------
# Markdown 通用解析
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.S)


def _split_headings(text: str) -> list[tuple[int, str, str]]:
    """按标题切分文档，返回 [(level, title, body), ...]。"""
    parts: list[tuple[int, str, str]] = []
    current: tuple[int, str] | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if current is not None:
                parts.append((current[0], current[1], "\n".join(buf)))
            current = (len(m.group(1)), m.group(2).strip())
            buf = []
        else:
            buf.append(line)
    if current is not None:
        parts.append((current[0], current[1], "\n".join(buf)))
    return parts


def _extract_code_blocks(text: str) -> list[CodeBlock]:
    return [CodeBlock(language=m.group(1).strip(), content=m.group(2).strip())
            for m in _FENCE_RE.finditer(text)]


def _parse_table_rows(rows: list[str]) -> Table | None:
    """解析 |a|b| 形式的表格行，兼容分隔行（---）。"""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    if len(cells) < 2:
        return None
    headers = cells[0]
    body = cells[2:] if (len(cells) > 1 and
                         all(re.fullmatch(r":?-{3,}:?", c) for c in cells[1])) else cells[1:]
    return Table(headers=headers, rows=body)


def _extract_tables(text: str) -> list[Table]:
    tables: list[Table] = []
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip().startswith("|"):
            j = i
            rows = []
            while j < n and lines[j].strip().startswith("|"):
                rows.append(lines[j].strip())
                j += 1
            if len(rows) >= 2:
                t = _parse_table_rows(rows)
                if t:
                    tables.append(t)
            i = j
        else:
            i += 1
    return tables


def _extract_lists(text: str) -> list[str]:
    return [m.group(1).strip() for line in text.splitlines()
            if (m := _BULLET_RE.match(line))]


def _parse_section(level: int, title: str, body: str) -> Section:
    m = re.match(r"^([A-Z]|\d+)[.)]\s+(.*)$", title)
    sec_id, sec_title = (m.group(1), m.group(2)) if m else ("", title)
    return Section(
        id=sec_id, title=sec_title, level=level, content_md=body,
        tables=_extract_tables(body),
        code_blocks=_extract_code_blocks(body),
        lists=_extract_lists(body),
    )


# ---------------------------------------------------------------------------
# 架构文档（Architecture_Documentation.md）语义抽取
# ---------------------------------------------------------------------------

_COMPONENT_BULLET_RE = re.compile(
    r"^\s*\*\s*([A-Za-z][A-Za-z0-9]*(?:Component|Service|Client))\s*:\s*(.*)$", re.M)


def _find_section(sections: list[Section], *, id_: str = "", title_kw: str = "") -> Section | None:
    for s in sections:
        if id_ and s.id == id_:
            return s
        if title_kw and title_kw.lower() in s.title.lower():
            return s
    return None


def _parse_risk_tables(sec: Section) -> tuple[list[dict], list[dict]]:
    risks, qa = [], []
    for t in sec.tables:
        head0 = t.headers[0].lower() if t.headers else ""
        if "risk" in head0:
            for r in t.rows:
                risks.append({"risk": r[0], "mitigation": r[1] if len(r) > 1 else ""})
        elif "asr" in head0:
            for r in t.rows:
                qa.append({"id": r[0], "test_type": r[1] if len(r) > 1 else ""})
    return risks, qa


def _parse_traceability(sec: Section) -> list[dict]:
    if not sec.tables:
        return []
    t = sec.tables[0]

    def col(*keys: str) -> int | None:
        for idx, h in enumerate(t.headers):
            hl = h.lower()
            if any(k in hl for k in keys):
                return idx
        return None

    i_id, i_text = col("requirement"), col("short")
    i_diag, i_comp = col("diagram"), col("component")
    i_art, i_rat = col("artifact"), col("rationale")
    rows = []
    for r in t.rows:
        def get(i: int | None) -> str:
            return r[i].strip() if i is not None and i < len(r) else ""
        rows.append({
            "id": get(i_id),
            "short_text": get(i_text),
            "diagrams": [d.strip() for d in get(i_diag).split(",") if d.strip()],
            "components": [c.strip() for c in get(i_comp).split(",") if c.strip()],
            "artifacts": [a.strip() for a in get(i_art).split(",") if a.strip()],
            "rationale": get(i_rat),
        })
    return rows


def _tech_options(bullets: list[str]) -> dict:
    """从 '名称: 值 (Justification: ...)' 形式的列表项抽取技术选型。"""
    opts: dict[str, str] = {}
    for b in bullets:
        if not b or b.startswith("Justification"):
            continue
        m = re.match(r"^([^:]+):\s*(.*)$", b)
        if m:
            opts[m.group(1).strip()] = m.group(2).strip()
    return opts


def _parse_component_detail(sections: list[Section]) -> dict:
    """解析 D 节下的一个组件小节及其下属 ### / #### 子小节。

    标题切分后，## 组件名 与它的 ### 子小节是独立 Section，
    需要合并回来才能拿到完整内容。
    """
    main = sections[0]
    detail: dict = {"name": main.title, "subsections": {}}
    detail["intro"] = main.content_md.strip()
    for s in sections[1:]:
        detail["subsections"][s.title] = {
            "lists": _extract_lists(s.content_md),
            "tables": [asdict(t) for t in _extract_tables(s.content_md)],
            "code_blocks": [asdict(c) for c in _extract_code_blocks(s.content_md)],
        }
    all_lists = [l for s in sections for l in _extract_lists(s.content_md)]
    detail["technology_options"] = _tech_options(all_lists)
    return detail


def parse_documentation_md(text: str) -> dict:
    sections = [_parse_section(lv, title, body) for lv, title, body in _split_headings(text)]

    sec_a = _find_section(sections, id_="A")
    risks, qa = _parse_risk_tables(sec_a) if sec_a else ([], [])
    style, deployment = "", ""
    if sec_a:
        if m := re.search(r"^Chosen architectural style:\s*(.+)$", sec_a.content_md, re.M):
            style = m.group(1).strip()
        if m := re.search(r"^Deployment topology:\s*(.+)$", sec_a.content_md, re.M):
            deployment = m.group(1).strip()

    sec_b = _find_section(sections, id_="B")
    traceability = _parse_traceability(sec_b) if sec_b else []

    components = []
    seen_names: set[str] = set()
    for s in sections:
        for m in _COMPONENT_BULLET_RE.finditer(s.content_md):
            if m.group(1) not in seen_names:
                seen_names.add(m.group(1))
                components.append({"name": m.group(1), "description": m.group(2).strip()})

    # D 节下的组件明细（## 级、无编号的小节，须位于顶级 D 节之内；
    # 连同其下 ### / #### 子小节一并收集）
    component_details = []
    current_top = ""
    i, n = 0, len(sections)
    while i < n:
        s = sections[i]
        if s.level == 1:
            current_top = s.id
            i += 1
        elif s.level == 2 and current_top == "D" and not s.id:
            merged = [s]
            j = i + 1
            while j < n and sections[j].level > 2:
                merged.append(sections[j])
                j += 1
            component_details.append(_parse_component_detail(merged))
            i = j
        else:
            i += 1

    deliverables: list[str] = []
    review_checklist: list[str] = []
    sec_l = _find_section(sections, id_="L")
    if sec_l:
        for cb in sec_l.code_blocks:
            if cb.language in ("markdown", "text", ""):
                deliverables += re.findall(r"^\s*([\w./-]+\.\w+)\s*$", cb.content, re.M)
    sec_review = _find_section(sections, title_kw="review")
    if sec_review:
        review_checklist = sec_review.lists

    return {
        "title": sections[0].title if sections else "",
        "executive_summary": {
            "architectural_style": style,
            "deployment_topology": deployment,
            "risks": risks,
            "qa_coverage": qa,
        },
        "sections": [asdict(s) for s in sections],
        "traceability": traceability,
        "components": components,
        "component_details": component_details,
        "deliverables": deliverables,
        "review_checklist": review_checklist,
    }


# ---------------------------------------------------------------------------
# 架构视图（Architecture_View.md）PlantUML 解析
# ---------------------------------------------------------------------------

_TOKEN = r"(?:\[[^\]]+\]|\([\w.]+\)|\"[\w .]+\"|[\w.\*]+)"
_ARROW = r"(<-->|<\.\.|<--|<->|-->>|-->|->>|--\*|\*--|o--|--o|->|--|\.\.>|\.\.|<-)"
_REL_RE = re.compile(
    r"^\s*(" + _TOKEN + r")\s*" + _ARROW + r"\s*(" + _TOKEN + r")\s*(?::\s*(.*?))?\s*$")
_MEMBER_RE = re.compile(r"^([-+#])\s*([\w.]+)\s*(?:\(\s*([^)]*)\s*\))?\s*(?::\s*(.*?))?\s*$")


def _norm_token(tok: str) -> str:
    if tok.startswith("[") and tok.endswith("]"):
        return tok[1:-1]
    if tok.startswith("(") and tok.endswith(")"):
        return tok[1:-1]
    if tok.startswith('"'):
        return tok.strip('"')
    return tok


def _parse_plantuml(raw: str, view: str, kind: str) -> PlantUmlDiagram:
    d = PlantUmlDiagram(name="", view=view, kind=kind, raw=raw)
    stack: list[tuple[str, str]] = []  # (块类型, 块名)，用于 class 块与 package 块

    def add(cat: str, item: dict) -> None:
        d.elements.setdefault(cat, []).append(item)

    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("'"):
            continue
        if s.startswith("@startuml"):
            d.name = s[len("@startuml"):].strip()
            continue
        if s.startswith("@enduml") or s.startswith("@end") or s.startswith("!") or s.startswith("@"):
            continue
        if s in ("left to right direction", "top to bottom direction") or s.startswith("title "):
            continue

        if m := re.match(r'^actor\s+(\S+)(?:\s+as\s+(.+?))?\s*$', s):
            name = m.group(1).strip('"')
            label = (m.group(2) or "").strip().strip('"')
            add("actors", {"name": name, "alias": name, "label": label})
            continue
        if m := re.match(r'^usecase\s+"([^"]+)"\s+as\s+\((\w+)\)$', s):
            add("usecases", {"name": m.group(2), "label": m.group(1)})
            continue
        if m := re.match(r'^participant\s+(\S+)(?:\s+as\s+(.+?))?\s*$', s):
            name = m.group(1).strip('"')
            label = (m.group(2) or "").strip().strip('"')
            add("participants", {"name": name, "alias": name, "label": label})
            continue
        if m := re.match(r'^state\s+"?([\w.]+)"?(?:\s+as\s+([\w.]+))?(?::\s*(.*))?$', s):
            add("states", {"name": m.group(1), "label": m.group(3) or ""})
            continue
        if m := re.match(r'^(node|artifact|database|queue)\s+([\w.]+)(?:\s+as\s+([\w.]+))?$', s):
            add("nodes" if m.group(1) == "node" else "artifacts",
                {"name": m.group(2), "kind": m.group(1)})
            continue
        if m := re.match(r'^class\s+([\w.]+)\s*\{$', s):
            entry = {"name": m.group(1), "members": []}
            if stack and stack[-1][0] in ("package", "rectangle", "frame"):
                entry["package"] = stack[-1][1]
            add("classes", entry)
            stack.append(("class", m.group(1)))
            continue
        if (m := re.match(r'^class\s+([\w.]+)\s*$', s)) and stack and stack[-1][0] != "class":
            add("classes", {"name": m.group(1), "members": [], "package": stack[-1][1]})
            continue
        if m := re.match(r'^(package|rectangle|frame)\s+([\w.]+)\s*\{$', s):
            add("packages", {"name": m.group(2), "kind": m.group(1)})
            stack.append((m.group(1), m.group(2)))
            continue
        if s == "}":
            if stack:
                stack.pop()
            continue
        if (m := _MEMBER_RE.match(s)) and stack and stack[-1][0] == "class":
            d.elements["classes"][-1]["members"].append({
                "visibility": m.group(1), "name": m.group(2),
                "params": (m.group(3) or "").strip(), "type": (m.group(4) or "").strip(),
            })
            continue

        # 活动图流程元素
        if s == "start":
            d.flow.append({"kind": "start"})
            continue
        if s == "stop":
            d.flow.append({"kind": "stop"})
            continue
        if m := re.match(r"^:([^;]+);$", s):
            d.flow.append({"kind": "action", "text": m.group(1).strip()})
            continue
        if m := re.match(r"^if\s*\(([^)]*)\)\s*then\s*\(([^)]*)\)$", s):
            d.flow.append({"kind": "if", "condition": m.group(1).strip(),
                           "label": m.group(2).strip()})
            continue
        if m := re.match(r"^else(?:if\s*\(([^)]*)\))?(?:\s*\(([^)]*)\))?$", s):
            d.flow.append({"kind": "else", "condition": (m.group(1) or "").strip(),
                           "label": (m.group(2) or "").strip()})
            continue
        if s == "endif":
            d.flow.append({"kind": "endif"})
            continue
        if re.match(r"^note\s+(?:left|right|top|bottom)\s+", s):
            d.notes.append(s)
            continue

        if m := _REL_RE.match(s):
            d.relations.append({
                "from": _norm_token(m.group(1)),
                "to": _norm_token(m.group(3)),
                "arrow": m.group(2),
                "label": (m.group(4) or "").strip(),
            })
            continue

        # 无法归类 → 保留原文，宽容处理
        d.flow.append({"kind": "raw", "text": s})

    return d


def parse_views_md(text: str) -> dict:
    diagrams: list[PlantUmlDiagram] = []
    current_view, current_kind = "", ""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if m := re.match(r"^##\s+(.*)$", line):
            current_view = m.group(1).strip()
            continue
        if m := re.match(r"^\s*(\d+)\.\s*(.*)$", line):
            current_kind = m.group(2).strip()
            continue
        if line.strip() == "```plantuml":
            buf: list[str] = []
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == "```":
                    break
                buf.append(lines[j])
            diagrams.append(_parse_plantuml("\n".join(buf), current_view, current_kind))
    return {"diagrams": [asdict(d) for d in diagrams]}


# ---------------------------------------------------------------------------
# 缺失/矛盾分析
# ---------------------------------------------------------------------------

# 整词匹配的提示词（\b 词边界，防止 'ui' 命中 requirements、'spa' 命中 Space）
_UI_WORD_HINTS = ("browser", "ui", "screen", "menu", "frontend", "spa",
                  "gameplay", "dashboard")
# 子串匹配的提示词（中文与带连字符的词）
_UI_SUBSTR_HINTS = ("user-facing", "front-end", "single-page",
                    "界面", "菜单", "屏幕", "浏览器", "前端", "主页", "答题界面")


def _detect_ui_requirement(doc: dict, views: dict) -> bool:
    """文档是否描述了浏览器/用户界面类功能（决定计划是否需要前端文件）。

    扫描正文标题/内容（键为 title/content_md）、用例图元素名与标签、
    活动图流程文本与图注中的关键词（如介绍/主菜单/答题/结束反馈）。
    """
    pool: list[str] = []
    for s in doc.get("sections", []):
        pool.append(str(s.get("title", "")))
        pool.append(str(s.get("content_md", "")))
    for d in views.get("diagrams", []):
        elements = d.get("elements")
        if isinstance(elements, dict):
            for items in elements.values():
                if isinstance(items, list):
                    for el in items:
                        if isinstance(el, dict):
                            pool.append(str(el.get("name", "")))
                            pool.append(str(el.get("label", "")))
                        else:
                            pool.append(str(el))
        for step in d.get("flow", []):
            if isinstance(step, dict):
                pool.extend(str(v) for v in step.values())
        notes = d.get("notes", [])
        if isinstance(notes, list):
            pool.extend(str(n) for n in notes)
    blob = "\n".join(pool)
    low = blob.lower()
    if any(h in low for h in _UI_SUBSTR_HINTS):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in _UI_WORD_HINTS)


def analyze(doc: dict, views: dict) -> dict:
    gaps: list[dict] = []
    diag_names = {d["name"] for d in views["diagrams"]}
    view_text = "\n".join(d["raw"] for d in views["diagrams"])
    comp_doc = {c["name"] for c in doc["components"]}
    comp_view = set(re.findall(r"(\w+Component)", view_text))
    deliverables = set(doc["deliverables"])

    for tr in doc["traceability"]:
        rid = tr["id"]
        for dn in tr["diagrams"]:
            if dn not in diag_names:
                gaps.append({"type": "error",
                             "message": f"需求 {rid} 追溯的图 \"{dn}\" 在 Architecture_View.md 中不存在"})
        for cn in tr["components"]:
            if cn not in comp_view:
                gaps.append({"type": "warning",
                             "message": f"需求 {rid} 追溯的组件 \"{cn}\" 未出现在任何组件图中"})
            if cn not in comp_doc:
                gaps.append({"type": "info",
                             "message": f"组件 \"{cn}\" 只在图中出现，架构文档正文未单列介绍"})
        for an in tr["artifacts"]:
            if deliverables and an not in deliverables:
                gaps.append({"type": "warning",
                             "message": f"需求 {rid} 引用的交付物 \"{an}\" 不在交付物清单中"
                                        f"（清单: {sorted(deliverables)}）"})

    for cn in sorted(comp_view - comp_doc):
        gaps.append({"type": "info", "message": f"组件图包含组件 \"{cn}\"，但架构文档正文未介绍"})

    stats = {
        "sections": len(doc["sections"]),
        "tables": sum(len(s["tables"]) for s in doc["sections"]),
        "code_blocks": sum(len(s["code_blocks"]) for s in doc["sections"]),
        "requirements": len(doc["traceability"]),
        "components": len(doc["components"]),
        "deliverables": len(deliverables),
        "diagrams": len(views["diagrams"]),
        "relations": sum(len(d["relations"]) for d in views["diagrams"]),
        "gaps_by_type": {},
    }
    for g in gaps:
        stats["gaps_by_type"][g["type"]] = stats["gaps_by_type"].get(g["type"], 0) + 1
    return {"gaps": gaps, "stats": stats,
            "has_ui_requirement": _detect_ui_requirement(doc, views)}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def preprocess(doc_path: str | Path, views_path: str | Path) -> dict:
    """解析两份架构文档，返回结构化 JSON。"""
    doc = parse_documentation_md(Path(doc_path).read_text(encoding="utf-8"))
    views = parse_views_md(Path(views_path).read_text(encoding="utf-8"))
    return {
        "source": {"documentation": str(Path(doc_path).resolve()),
                   "views": str(Path(views_path).resolve())},
        "documentation": doc,
        "views": views,
        "analysis": analyze(doc, views),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="架构文档 → 结构化 JSON（CodeAgent 第 3 步）")
    p.add_argument("doc", help="Architecture_Documentation.md 路径")
    p.add_argument("views", help="Architecture_View.md 路径")
    p.add_argument("-o", "--output", default="architecture_input.json", help="输出 JSON 路径")
    args = p.parse_args(argv)

    result = preprocess(args.doc, args.views)
    out = Path(args.output)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = result["analysis"]["stats"]
    print(f"已解析: {stats['sections']} 节 / {stats['tables']} 表 / "
          f"{stats['code_blocks']} 代码块 / {stats['diagrams']} 张图 / "
          f"{stats['requirements']} 条需求 / 发现 {len(result['analysis']['gaps'])} 个问题")
    for g in result["analysis"]["gaps"]:
        print(f"  [{g['type']}] {g['message']}")
    print(f"输出: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
