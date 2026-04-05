import os

import asyncpg
import pytest

from db.repositories import message_repo

TEST_DB_URL = os.getenv("DATABASE_URL")


async def create_user(conn: asyncpg.Connection, username: str, email: str) -> int:
    return await conn.fetchval(
        "INSERT INTO user_account (username, password, email) VALUES ($1, $2, $3) RETURNING user_id;",
        username,
        "pass123",
        email,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_quote_count_persists_after_local_delete():
    conn = await asyncpg.connect(TEST_DB_URL)
    try:
        user_a = await create_user(conn, "user_a", "a@example.com")
        user_b = await create_user(conn, "user_b", "b@example.com")

        conv_id = await conn.fetchval("INSERT INTO conversation (type) VALUES ('private') RETURNING conversation_id;")
        await conn.execute(
            "INSERT INTO conversation_member (conversation_id, member_user_id) VALUES ($1, $2), ($1, $3);",
            conv_id,
            user_a,
            user_b,
        )

        original_msg = await message_repo.db_send_message(conn, user_a, conv_id, {"text": "hello"})
        assert original_msg["seq_id"] == 1

        quoted_msg = await message_repo.db_quote_message(conn, user_b, conv_id, {"text": "quoted"}, original_msg["msg_id"])
        assert quoted_msg["seq_id"] == 2

        quote_count = await conn.fetchval("SELECT quote_count FROM message WHERE msg_id = $1", original_msg["msg_id"])
        assert quote_count == 1

        await message_repo.db_delete_local_messages(conn, user_b, conv_id, [original_msg["msg_id"]])
        inbox_count = await conn.fetchval(
            "SELECT COUNT(*) FROM user_inbox WHERE user_id = $1 AND conversation_id = $2 AND msg_id = $3",
            user_b,
            conv_id,
            original_msg["msg_id"],
        )
        assert inbox_count == 0

        quote_count_after_delete = await conn.fetchval(
            "SELECT quote_count FROM message WHERE msg_id = $1", original_msg["msg_id"])
        assert quote_count_after_delete == 1

        history_for_b = await message_repo.db_get_message_history(conn, user_b, conv_id)
        assert all(row["msg_id"] != original_msg["msg_id"] for row in history_for_b)
        assert any(row["msg_id"] == quoted_msg["msg_id"] for row in history_for_b)

        history_for_a = await message_repo.db_get_message_history(conn, user_a, conv_id)
        assert any(row["msg_id"] == original_msg["msg_id"] for row in history_for_a)
    finally:
        await conn.close()
