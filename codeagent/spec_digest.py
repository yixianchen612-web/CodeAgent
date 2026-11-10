"""规格摘要器：把 preprocessor 的结构化 JSON 压缩成 8-12KB 的 digest。

调研结论：原始 Markdown 不直接进 LLM，只送结构化摘要（静态提示词 + 动态数据分离）。
保留：执行摘要、风险、追溯矩阵、组件与技术选型、API/SQL 契约代码块、交付物、
图的结构（元素/关系，去掉 raw PlantUML）、问题分析。
"""

from __future__ import annotations

import json
from typing import Any

MAX_CHARS = 12000
MAX_RELATIONS_PER_DIAGRAM = 12
MAX_SUBSECTION_LISTS = 6


def _trim(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def build_digest(spec: dict) -> dict:
    doc = spec.get("documentation") or {}
    views = spec.get("views") or {}

    diagrams = []
    for d in views.get("diagrams", []):
        elements = {}
        for cat, items in (d.get("elements") or {}).items():
            elements[cat] = [{"name": i.get("name"),
                              "members": i.get("members")} if cat == "classes" and i.get("members")
                             else i.get("name")
                             for i in items]
        diagrams.append({
            "name": d.get("name"),
            "kind": _trim(d.get("kind"), 80),
            "elements": elements,
            "relations": d.get("relations", [])[:MAX_RELATIONS_PER_DIAGRAM],
            "flow": [f for f in d.get("flow", []) if f.get("kind") != "raw"][:20],
            "notes": d.get("notes", [])[:5],
        })

    components = []
    for c in doc.get("components", []):
        components.append({"name": c.get("name"), "description": _trim(c.get("description"), 200)})

    details = []
    for cd in doc.get("component_details", []):
        subsections = {}
        for title, sub in (cd.get("subsections") or {}).items():
            subsections[_trim(title, 60)] = {
                "lists": [l for l in sub.get("lists", [])[:MAX_SUBSECTION_LISTS]
                          if "Justification" not in l],
                "code_blocks": sub.get("code_blocks", []),
            }
        details.append({"name": cd.get("name"),
                        "technology_options": cd.get("technology_options", {}),
                        "subsections": subsections})

    digest: dict[str, Any] = {
        "title": doc.get("title", ""),
        "executive_summary": doc.get("executive_summary", {}),
        "traceability": doc.get("traceability", []),
        "components": components,
        "component_details": details,
        "deliverables": doc.get("deliverables", []),
        "review_checklist": doc.get("review_checklist", []),
        "diagrams": diagrams,
        "analysis": spec.get("analysis", {}),
    }
    # 超长保护：整体压缩到 MAX_CHARS 内
    text = json.dumps(digest, ensure_ascii=False)
    if len(text) > MAX_CHARS:
        digest["_truncated"] = True
        for d in diagrams:
            d["relations"] = d["relations"][:6]
        digest["component_details"] = [
            {"name": c["name"], "technology_options": c.get("technology_options", {}),
             "subsections": {k: {"code_blocks": v.get("code_blocks", [])}
                            for k, v in c.get("subsections", {}).items()}}
            for c in digest["component_details"]
        ]
    return digest
