"""
Clean up test fixture accounts and test documents from Cortex DB and Qdrant vector store.
Keeps core operational users:
- admin
- manager_eng
- employee_eng
- manager_hr
- employee_hr
- manager_fin
- employee_fin
"""

import asyncio
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from qdrant_client import QdrantClient
from backend.core.config import settings
from backend.db.qdrant import get_qdrant_client, init_qdrant_collection

CORE_USERS = (
    'admin',
    'manager_eng',
    'employee_eng',
    'manager_hr',
    'employee_hr',
    'manager_fin',
    'employee_fin',
)

async def cleanup_database():
    print("=" * 60)
    print("CORTEX DATABASE & VECTOR STORE CLEANUP")
    print("=" * 60)

    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        # 1. Identify test users
        core_placeholders = ", ".join(f"'{u}'" for u in CORE_USERS)
        test_users_res = await conn.execute(text(
            f"SELECT id, username FROM users WHERE username NOT IN ({core_placeholders});"
        ))
        test_users = test_users_res.fetchall()
        test_user_ids = [str(r[0]) for r in test_users]
        print(f"Found {len(test_user_ids)} test fixture user(s) to remove.")

        if test_user_ids:
            id_list_str = ", ".join(f"'{uid}'" for uid in test_user_ids)

            # Delete message_feedback linked to test users or test conversations
            mf_del = await conn.execute(text(f"""
                DELETE FROM message_feedback 
                WHERE user_id IN ({id_list_str})
                   OR message_id IN (
                       SELECT m.id FROM messages m 
                       JOIN conversations c ON m.conversation_id = c.id 
                       WHERE c.user_id IN ({id_list_str})
                   );
            """))
            print(f" - Deleted {mf_del.rowcount} test message_feedback row(s).")

            # Delete messages linked to test conversations
            msg_del = await conn.execute(text(f"""
                DELETE FROM messages 
                WHERE conversation_id IN (
                    SELECT id FROM conversations WHERE user_id IN ({id_list_str})
                );
            """))
            print(f" - Deleted {msg_del.rowcount} test message(s).")

            # Delete tickets
            tkt_del = await conn.execute(text(f"""
                DELETE FROM tickets WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {tkt_del.rowcount} test ticket(s).")

            # Delete action_drafts
            draft_del = await conn.execute(text(f"""
                DELETE FROM action_drafts WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {draft_del.rowcount} test action draft(s).")

            # Delete conversations
            conv_del = await conn.execute(text(f"""
                DELETE FROM conversations WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {conv_del.rowcount} test conversation(s).")

            # Delete token_usage_logs
            tok_del = await conn.execute(text(f"""
                DELETE FROM token_usage_logs WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {tok_del.rowcount} test token usage log(s).")

            # Delete leave_balances for test users
            leave_del = await conn.execute(text(f"""
                DELETE FROM leave_balances WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {leave_del.rowcount} test leave balance row(s).")

            # Delete audit_logs for test users
            audit_del = await conn.execute(text(f"""
                DELETE FROM audit_logs WHERE user_id IN ({id_list_str});
            """))
            print(f" - Deleted {audit_del.rowcount} test audit log(s).")

            # Finally, delete test users
            user_del = await conn.execute(text(f"""
                DELETE FROM users WHERE id IN ({id_list_str});
            """))
            print(f" - Deleted {user_del.rowcount} test user(s).")

        # 2. Clean up test documents
        # All 82 docs in DB right now are test fixture policies generated during pytest runs
        doc_count_res = await conn.execute(text("SELECT count(*) FROM documents;"))
        doc_count = doc_count_res.scalar()
        print(f"\nFound {doc_count} test document(s) in DB to remove.")

        if doc_count > 0:
            doc_del = await conn.execute(text("DELETE FROM documents;"))
            print(f" - Deleted {doc_del.rowcount} test document(s) from PostgreSQL.")

        # Verify core users remaining
        remaining_users = await conn.execute(text("SELECT username, role, department FROM users ORDER BY username;"))
        print("\nRemaining Core Users in DB:")
        for u in remaining_users.fetchall():
            print(f"   [OK] {u[0]} (Role: {u[1]}, Dept: {u[2]})")

    await engine.dispose()

    # 3. Clean up Qdrant vector store collection
    print("\nResetting Qdrant vector store collection...")
    try:
        qc = get_qdrant_client()
        if qc.collection_exists(settings.QDRANT_COLLECTION):
            qc.delete_collection(settings.QDRANT_COLLECTION)
            print(f" [OK] Deleted existing Qdrant collection '{settings.QDRANT_COLLECTION}'.")
        init_qdrant_collection(qc)
        print(f" [OK] Qdrant collection '{settings.QDRANT_COLLECTION}' re-initialized and clean.")
    except Exception as exc:
        print(f" [WARN] Qdrant reset failed: {exc}")

    print("\nCleanup completed successfully!")

if __name__ == "__main__":
    asyncio.run(cleanup_database())
