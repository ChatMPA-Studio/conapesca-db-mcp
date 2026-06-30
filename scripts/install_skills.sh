#!/usr/bin/env bash
# =============================================================================
# install_skills.sh — Install CONAPESCA skills into ~/.claude/skills/
#
# Usage:
#   bash scripts/install_skills.sh            # install
#   bash scripts/install_skills.sh --uninstall
#
# After installation, skills are available as /conapesca-<skill-name>
# Without installation, skills are available via MCP as
#   /mcp__conapesca__conapesca-<skill-name>
# =============================================================================

set -euo pipefail

GITHUB_ORG="ChatMPA-Studio"
GITHUB_REPO="conapesca-db-mcp"
SKILLS_DIR="${HOME}/.claude/skills"
SKIP_DIRS=("contracts" "example-workflow" "healthcheck")

uninstall=false
[[ "${1:-}" == "--uninstall" ]] && uninstall=true

if $uninstall; then
    echo "Uninstalling ${GITHUB_REPO} skills..."
    gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/contents/skills" --jq '.[].name' | \
        while read -r skill; do
            [[ " ${SKIP_DIRS[*]} " =~ " ${skill} " ]] && continue
            rm -rf "${SKILLS_DIR}/${skill}"
            echo "Removed: ${skill}"
        done
    exit 0
fi

echo "Installing ${GITHUB_REPO} skills to ${SKILLS_DIR}..."
mkdir -p "${SKILLS_DIR}"

gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/contents/skills" --jq '.[].name' | \
    while read -r skill; do
        [[ " ${SKIP_DIRS[*]} " =~ " ${skill} " ]] && continue
        mkdir -p "${SKILLS_DIR}/${skill}"

        gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/contents/skills/${skill}/SKILL.md" \
            --jq '.content' | base64 -d > "${SKILLS_DIR}/${skill}/SKILL.md"

        if gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/contents/skills/${skill}/references" \
           --jq '.[].name' 2>/dev/null | \
            while read -r ref; do
                mkdir -p "${SKILLS_DIR}/${skill}/references"
                gh api "repos/${GITHUB_ORG}/${GITHUB_REPO}/contents/skills/${skill}/references/${ref}" \
                    --jq '.content' | base64 -d > "${SKILLS_DIR}/${skill}/references/${ref}"
            done; then
            :
        fi

        echo "Installed: ${skill}"
    done

echo "Done. Restart Claude Code to load new skills."
echo "Skills are now available as /<skill-name>"
