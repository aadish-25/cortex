"""
Cortex — Jira Cloud REST API v3 Integration Prototype & Demo Script.
Demonstrates:
  1. Authentication verification (GET /rest/api/3/myself)
  2. Project list introspection (GET /rest/api/3/project)
  3. Issue creation schema introspection (GET /rest/api/3/issue/createmeta)
  4. Real ticket creation using Atlassian Document Format (POST /rest/api/3/issue)
  5. Ticket status retrieval (GET /rest/api/3/issue/{issue_id_or_key})

Run:
  uv run python scripts/demo_jira_connection.py
"""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure cortex root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class JiraConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    JIRA_BASE_URL: str = ""
    JIRA_API_EMAIL: str = ""
    JIRA_API_TOKEN: str = ""
    JIRA_DEFAULT_PROJECT_KEY: str = ""


class JiraDemoClient:
    """Async client for Atlassian Jira Cloud REST API v3."""

    def __init__(self, base_url: str, email: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email.strip()
        self.token = token.strip()
        auth_bytes = f"{self.email}:{self.token}".encode("utf-8")
        self.auth_header = f"Basic {base64.b64encode(auth_bytes).decode('utf-8')}"
        self.headers = {
            "Authorization": self.auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def get_myself(self) -> Dict[str, Any]:
        """Validates credentials and retrieves current authenticated Jira user."""
        url = f"{self.base_url}/rest/api/3/myself"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Jira Auth Failed (HTTP {resp.status_code}): {resp.text}\n"
                    f"Check JIRA_BASE_URL ({self.base_url}), JIRA_API_EMAIL ({self.email}), and JIRA_API_TOKEN."
                )
            return resp.json()

    async def list_projects(self) -> List[Dict[str, Any]]:
        """Lists accessible projects in Jira instance."""
        url = f"{self.base_url}/rest/api/3/project"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to list projects (HTTP {resp.status_code}): {resp.text}")
            return resp.json()

    async def get_create_meta(self, project_key: str) -> Dict[str, Any]:
        """Introspects issue types and required fields for a target project."""
        url = f"{self.base_url}/rest/api/3/issue/createmeta"
        params = {
            "projectKeys": project_key,
            "expand": "projects.issuetypes.fields",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self.headers, params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to introspect create metadata (HTTP {resp.status_code}): {resp.text}")
            return resp.json()

    async def create_issue(
        self,
        project_key: str,
        issue_type_name: str,
        summary: str,
        description_text: str,
    ) -> Dict[str, Any]:
        """
        Creates an issue in Jira Cloud using Atlassian Document Format (ADF) description.
        """
        url = f"{self.base_url}/rest/api/3/issue"

        # Atlassian Document Format (ADF) for Jira v3 REST API
        adf_description = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": description_text,
                        }
                    ],
                }
            ],
        }

        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": adf_description,
                "issuetype": {"name": issue_type_name},
            }
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create Jira issue (HTTP {resp.status_code}): {resp.text}")
            return resp.json()

    async def get_issue(self, issue_key_or_id: str) -> Dict[str, Any]:
        """Retrieves created ticket details and status."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key_or_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to fetch issue {issue_key_or_id} (HTTP {resp.status_code}): {resp.text}")
            return resp.json()


def print_setup_instructions():
    print("=" * 80)
    print(" ⚠️ JIRA CLOUD CREDENTIALS MISSING IN CORTEX .env")
    print("=" * 80)
    print("To connect Cortex to your real Jira workspace, add the following to cortex/.env:")
    print()
    print("  JIRA_BASE_URL=https://your-domain.atlassian.net")
    print("  JIRA_API_EMAIL=your-email@company.com")
    print("  JIRA_API_TOKEN=your_atlassian_api_token_here")
    print("  JIRA_DEFAULT_PROJECT_KEY=ITSD  # (Optional: defaults to first accessible project)")
    print()
    print("HOW TO GET THEM IN 2 MINUTES:")
    print("1. If you don't have Jira, get a 100% free Cloud instance (free forever up to 10 users):")
    print("   👉 https://www.atlassian.com/software/jira/free")
    print("2. Create an Atlassian API Token (instant 30-second setup):")
    print("   👉 https://id.atlassian.com/manage-profile/security/api-tokens")
    print("   Click 'Create API token', name it 'cortex-dev', and copy the generated token.")
    print("3. Paste the three variables into cortex/.env and re-run this script!")
    print("=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Cortex Jira Cloud Integration Spike")
    parser.add_argument("--url", default=None, help="Jira Cloud base URL")
    parser.add_argument("--email", default=None, help="Jira account email")
    parser.add_argument("--token", default=None, help="Atlassian API token")
    parser.add_argument("--project", default=None, help="Target project key (e.g. ITSD, ENG, KAN)")
    parser.add_argument("--create-ticket", action="store_true", help="Create a live test ticket")
    args = parser.parse_args()

    cfg = JiraConfig()
    base_url = args.url or cfg.JIRA_BASE_URL or os.environ.get("JIRA_BASE_URL", "")
    email = args.email or cfg.JIRA_API_EMAIL or os.environ.get("JIRA_API_EMAIL", "")
    token = args.token or cfg.JIRA_API_TOKEN or os.environ.get("JIRA_API_TOKEN", "")

    if not base_url or not email or not token:
        print_setup_instructions()
        sys.exit(1)

    print("=" * 80)
    print(" CORTEX (AAD-H1) — JIRA CLOUD REST API v3 CONNECTION TEST")
    print(f" Target Instance: {base_url}")
    print(f" User Identity  : {email}")
    print("=" * 80)

    client = JiraDemoClient(base_url, email, token)

    # 1. Verify Authentication
    print("\n[STEP 1/4] Verifying Jira Authentication...")
    try:
        user_info = await client.get_myself()
        print(f"  ✅ Connected successfully as: {user_info.get('displayName')} ({user_info.get('emailAddress', email)})")
        print(f"  Account ID: {user_info.get('accountId')}")
        print(f"  Timezone  : {user_info.get('timeZone')}")
    except Exception as e:
        print(f"  ❌ Authentication check failed: {e}")
        sys.exit(1)

    # 2. List Projects
    print("\n[STEP 2/4] Introspecting Accessible Jira Projects...")
    projects = await client.list_projects()
    if not projects:
        print("  ⚠️ No projects found in this Jira instance. Please create at least one project in Jira.")
        sys.exit(1)

    print(f"  Found {len(projects)} accessible project(s):")
    for p in projects:
        print(f"  • Key: {p.get('key'):<8} | Name: {p.get('name'):<30} | Type: {p.get('projectTypeKey', 'standard')}")

    target_project = args.project or cfg.JIRA_DEFAULT_PROJECT_KEY or projects[0].get("key")
    print(f"\n  🎯 Selected Target Project: {target_project}")

    # 3. Introspect Create Metadata & Issue Types
    print(f"\n[STEP 3/4] Introspecting Issue Types & Field Schemas for '{target_project}'...")
    try:
        meta = await client.get_create_meta(target_project)
        meta_projects = meta.get("projects", [])
        if not meta_projects:
            print(f"  ⚠️ Could not retrieve issue types for project '{target_project}'.")
            issue_types = []
        else:
            issue_types = meta_projects[0].get("issuetypes", [])

        print(f"  Available Issue Types in '{target_project}':")
        issue_type_names = []
        for it in issue_types:
            name = it.get("name")
            desc = it.get("description", "No description")
            is_subtask = it.get("subtask", False)
            issue_type_names.append(name)
            sub_tag = " (Subtask)" if is_subtask else ""
            print(f"  • [{name}]{sub_tag}: {desc[:60]}")

        # Pick issue type (prefer Task, Service Request, Bug, or first available)
        selected_type = None
        for candidate in ["Task", "Service Request", "IT Help", "Bug", "Story", "Incident"]:
            if candidate in issue_type_names:
                selected_type = candidate
                break
        if not selected_type and issue_type_names:
            selected_type = issue_type_names[0]

        print(f"\n  🎯 Recommended Issue Type for Testing: {selected_type}")
    except Exception as e:
        print(f"  ⚠️ Schema introspection notice: {e}")
        selected_type = "Task"

    # 4. Create Demo Ticket
    print(f"\n[STEP 4/4] Creating Demo Test Ticket in '{target_project}'...")
    test_summary = f"[Cortex Test] Integration Spike — {target_project}"
    test_description = (
        "This is an automated demo ticket created by Cortex (AAD-H1) "
        "to verify live Atlassian REST API v3 connectivity, schema compatibility, "
        "and Atlassian Document Format (ADF) description rendering."
    )

    try:
        new_issue = await client.create_issue(
            project_key=target_project,
            issue_type_name=selected_type,
            summary=test_summary,
            description_text=test_description,
        )
        issue_key = new_issue.get("key")
        issue_id = new_issue.get("id")
        issue_url = f"{base_url}/browse/{issue_key}"

        print(f"  🎉 TICKET CREATED SUCCESSFULLY IN JIRA!")
        print(f"  Ticket Key : {issue_key}")
        print(f"  Ticket ID  : {issue_id}")
        print(f"  Web Link   : {issue_url}")

        # Fetch verification
        fetched = await client.get_issue(issue_key)
        status_name = fetched.get("fields", {}).get("status", {}).get("name", "Unknown")
        print(f"  Live Status: {status_name}")

        print("\n" + "=" * 80)
        print(" JIRA INTEGRATION VERIFIED & READY FOR CORTEX PHASE 4.2!")
        print(f" Open your browser to view the created ticket: {issue_url}")
        print("=" * 80)
    except Exception as e:
        print(f"  ❌ Ticket creation failed: {e}")
        print("\nPossible causes:")
        print("  - The selected issue type requires additional mandatory custom fields.")
        print("  - User lacks 'Create Issues' permission in this project.")
        print("  - Target project is a Service Desk queue requiring a specific Request Type ID.")


if __name__ == "__main__":
    asyncio.run(main())
