# Skills Directory

Analytical skills for the CONAPESCA MCP Server. Each skill is a guided workflow
that orchestrates MCP tools to answer specific research questions about Mexican
fisheries landings data.

## Structure

```
skills/
├── README.md
├── registry.py                              # Developer catalog
├── contracts/                               # JSON Schema per skill
│   └── conapesca_temporal_trends.schema.json
└── conapesca-temporal-trends/
    └── SKILL.md                             # Species trend analysis
```

## Invocation

| Method | Command | Requires |
|--------|---------|---------|
| MCP Prompt | `/mcp__conapesca__conapesca-temporal-trends` | MCP URL in config |
| Local install | `/conapesca-temporal-trends` | `bash scripts/install_skills.sh` |

## Adding a New Skill

1. Create `skills/your-skill-name/SKILL.md`
2. Add a contract in `skills/contracts/your_skill.schema.json`
3. Register it in `skills/registry.py`
4. Restart the server — it appears automatically as an MCP prompt
