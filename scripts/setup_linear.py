#!/usr/bin/env python3
"""Create Glasses AI Linear issues (idempotent) and write data/linear_issues.json.

Usage:
  python scripts/setup_linear.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.linear_client import LinearError, list_teams, resolve_team, upsert_issue_by_title

OUT = ROOT / "data" / "linear_issues.json"
ENV_PATH = ROOT / ".env"

SEED = [
    {
        "owner": "Alex",
        "role": "workstream",
        "title": "[Alex] MenuOCR confidence gate + TTS summary",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/3",
        "description": (
            "## Owner\nAlex\n\n## Scope\nOn-device MenuOCR confidence gate (≥0.72) + short TTS menu summary.\n\n"
            "## Acceptance\n- ≥85% top-3 dish extraction on 20 golden menus\n"
            "- p95 first-glyph <250ms with ASR idle\n"
            "- false-speak rate <5%\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/3\n"
        ),
    },
    {
        "owner": "Alex",
        "role": "workstream",
        "title": "[Alex] Serif font pack + thermal yield signal",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/4",
        "description": (
            "## Owner\nAlex\n\n## Scope\nSerif/script font pack packaging + yield OCR when speech_active.\n\n"
            "## Blockers\nThermal contention with ASR; font license review.\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/4\n"
        ),
    },
    {
        "owner": "Sam",
        "role": "workstream",
        "title": "[Sam] Wake-word tighten + on-device ASR intents",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/5",
        "description": (
            "## Owner\nSam\n\n## Scope\nWWv3 threshold schedule, 3 wakes/min + LED, intents: read_menu / whats_around / stop.\n\n"
            "## Acceptance\nCafe false-wake ≤1.2/hour; intent precision ≥0.9.\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/5\n"
        ),
    },
    {
        "owner": "Sam",
        "role": "workstream",
        "title": "[Sam] Tap-to-share privacy stub (no continuous cloud)",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/6",
        "description": (
            "## Owner\nSam\n\n## Scope\nLocal 8s buffer; upload only on explicit confirm. Zero continuous cloud audio.\n\n"
            "## Policy\nPrivacy review: cloud ASR only on tap-to-share.\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/6\n"
        ),
    },
    {
        "owner": "Jordan",
        "role": "workstream",
        "title": "[Jordan] Dining/transit categorizer v0 + eval harness",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/7",
        "description": (
            "## Owner\nJordan\n\n## Scope\nDining vs transit vs unknown with confidence; offline eval harness.\n\n"
            "## Acceptance\nDining recall ≥0.75 on proxy set; never label below 0.55 conf.\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/7\n"
        ),
    },
    {
        "owner": "Jordan",
        "role": "workstream",
        "title": "[Jordan] Feature-bus fusion (ocr_tokens + asr_intent)",
        "github_url": "https://github.com/gjamesd/conference-crew/issues/8",
        "description": (
            "## Owner\nJordan\n\n## Scope\nLate fusion of OCR keywords + ASR intent + motion prior on shared feature bus.\n\n"
            "## Dependencies\nSam speech_active + asr_intent by Wednesday.\n\n"
            "## GitHub\nhttps://github.com/gjamesd/conference-crew/issues/8\n"
        ),
    },
    {
        "owner": "CrewBot",
        "role": "sprint_sync",
        "title": "CrewBot Sprint Sync",
        "github_url": None,
        "description": (
            "## Rolling sprint planning issue\n"
            "CrewBot updates this issue when someone asks for a meeting summary or sprint plan "
            "from the Conference Crew chat demo.\n"
        ),
    },
]


def _upsert_env(team_id: str, team_key: str) -> None:
    if not ENV_PATH.exists():
        return
    lines = ENV_PATH.read_text().splitlines(True)
    mapping = {"LINEAR_TEAM_ID": team_id, "LINEAR_TEAM_KEY": team_key}
    seen = set()
    out = []
    for line in lines:
        hit = False
        for k, v in mapping.items():
            if line.startswith(k + "="):
                out.append(f"{k}={v}\n")
                seen.add(k)
                hit = True
                break
        if not hit:
            out.append(line)
    for k, v in mapping.items():
        if k not in seen:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f"{k}={v}\n")
    ENV_PATH.write_text("".join(out))


def main() -> None:
    settings = get_settings()
    if not settings.linear_api_key:
        raise SystemExit("Set LINEAR_API_KEY in .env first")

    print("Teams:")
    for t in list_teams():
        print(f"  - {t.get('key')} · {t.get('name')} · {t.get('id')}")

    team = resolve_team()
    team_id = team["id"]
    team_key = team.get("key") or ""
    print(f"\nUsing team {team_key} ({team_id})")
    _upsert_env(team_id, team_key)

    records = []
    for seed in SEED:
        issue = upsert_issue_by_title(
            team_id=team_id,
            title=seed["title"],
            description=seed["description"],
        )
        rec = {
            "owner": seed["owner"],
            "role": seed["role"],
            "title": seed["title"],
            "github_url": seed["github_url"],
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "url": issue.get("url"),
        }
        records.append(rec)
        print(f"  upserted {rec['identifier']} · {rec['title']}")

    payload = {
        "team_id": team_id,
        "team_key": team_key,
        "issues": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {OUT}")
    print("Restart the API so Settings pick up LINEAR_TEAM_ID / LINEAR_TEAM_KEY.")


if __name__ == "__main__":
    try:
        main()
    except LinearError as exc:
        raise SystemExit(f"Linear error: {exc}") from exc
