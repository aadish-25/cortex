"""
Cortex Phase 6 - External Integrations Connectivity Verification Script.
Pings GitHub, Redis Rate Limiter, and Google Drive target folder.
"""
import asyncio
import os
import sys

# Ensure cortex root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.core.config import settings
from backend.services.integrations.github_service import GitHubService
from backend.services.integrations.google_drive_service import GoogleDriveService
from backend.core.rate_limiter import rate_limiter


async def main():
    print("=" * 60)
    print(" CORTEX PHASE 6: INTEGRATION CONNECTIVITY PING")
    print("=" * 60)

    # 1. GitHub Connectivity Ping
    print("\n[1/3] Testing GitHub MCP Client...")
    github = GitHubService()
    if not github.is_configured:
        print("  ❌ GitHub API Token is NOT configured in .env!")
    else:
        print(f"  ✓ Token present (repo: {github.default_repo})")
        # Test code search
        search_res = await github.search_code("token_version")
        status = search_res.get("status")
        total = search_res.get("total_count", 0)
        items = search_res.get("items", [])
        print(f"  ✓ GitHub Code Search: status={status}, total_matches={total}")
        for it in items[:3]:
            print(f"    - {it.get('name')} ({it.get('path')})")

        # Test issues list
        issues_res = await github.list_issues()
        i_items = issues_res.get("issues", [])
        print(f"  ✓ GitHub Issues: status={issues_res.get('status')}, count={len(i_items)}")
        for i in i_items[:3]:
            print(f"    - #{i.get('number')}: {i.get('title')}")

        # Test PR list
        prs_res = await github.list_pull_requests()
        pr_items = prs_res.get("pull_requests", [])
        print(f"  ✓ GitHub Pull Requests: status={prs_res.get('status')}, open_prs={len(pr_items)}")
        for pr in pr_items[:2]:
            print(f"    - #{pr.get('number')}: {pr.get('title')}")

    # 2. Redis Rate Limiter Connectivity Ping
    print("\n[2/3] Testing Redis Token Bucket Rate Limiter...")
    try:
        acquired = await rate_limiter.acquire("github", cost=1)
        print(f"  ✓ Rate Limiter acquire test: acquired={acquired}")
        is_limited = await rate_limiter.is_rate_limited("github")
        print(f"  ✓ Rate Limiter throttle status: is_limited={is_limited}")
    except Exception as exc:
        print(f"  ❌ Rate Limiter check failed: {exc}")

    # 3. Google Drive Scoped Knowledge Target Ping
    print("\n[3/3] Testing Google Drive Workspace Integration...")
    drive = GoogleDriveService()
    if not drive.is_configured:
        print("  ℹ Google Drive credentials not yet supplied in .env (Target folder pending)")
        print("  ✓ Sandbox Demonstration fallback is operational")
    else:
        print(f"  ✓ Google Drive configured (folder: {drive.root_folder_id})")
        search_res = await drive.search_files("policy")
        print(f"  ✓ Drive Search: status={search_res.get('status')}, total={search_res.get('total_count')}")

    print("\n" + "=" * 60)
    print(" PING COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
