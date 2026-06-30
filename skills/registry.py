"""Skills registry for CONAPESCA MCP server.

Catalogs available analytical skills with metadata, tool dependencies,
and input/output contracts. Developer reference only — not loaded at runtime.
"""

from typing import Dict, List, Optional

SKILLS_REGISTRY: Dict[str, Dict] = {
    "conapesca-temporal-trends": {
        "name": "Species Temporal Trends",
        "description": (
            "Annual landing trend for a species: time series, geographic breakdown "
            "by state or coast, and trend chart."
        ),
        "version": "1.1.0",
        "inputs_schema": "skills/contracts/conapesca_temporal_trends.schema.json",
        "outputs_schema": "skills/contracts/conapesca_temporal_trends.schema.json",
        "estimated_duration": "20 seconds",
        "tools_required": ["get_taxonomy", "get_landings"],
        "tags": ["temporal", "species", "geographic", "trends"],
    },
}


def list_skills() -> List[Dict]:
    return [{"id": sid, **meta} for sid, meta in SKILLS_REGISTRY.items()]


def get_skill(skill_id: str) -> Optional[Dict]:
    return SKILLS_REGISTRY.get(skill_id)


def list_skills_by_tag(tag: str) -> List[Dict]:
    return [
        {"id": sid, **meta}
        for sid, meta in SKILLS_REGISTRY.items()
        if tag in meta.get("tags", [])
    ]


def get_skill_count() -> int:
    return len(SKILLS_REGISTRY)
