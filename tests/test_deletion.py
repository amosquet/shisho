import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import asyncio


class MockRecord:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def get(self, key, default=None):
        return getattr(self, key, default)


class TestDeletion(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ["POCKETBASE_URL"] = "http://mock-pb:8090"
        os.environ["POCKETBASE_USER"] = "test@example.com"
        os.environ["POCKETBASE_PASSWORD"] = "password"
        os.environ["OWNER_ID"] = "123456789"

    # -------------------------------------------------------------
    # 1. Notes Deletion Tests
    # -------------------------------------------------------------
    @patch("cogs.notes.get_pb_client")
    @patch("cogs.notes.get_discord_user_id")
    async def test_delete_note_by_id(self, mock_get_user_id, mock_get_pb):
        from cogs.notes import Notes

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        note_record = MockRecord(id="note_rec_1", owner="pb_user_123", title="My Note Title", text="Content")
        pb_mock.collection.return_value.get_one.return_value = note_record

        bot_mock = MagicMock()
        cog = Notes(bot_mock)

        result = await cog.delete_note("discord_123", "note_rec_1")
        self.assertIn("Successfully deleted note: **My Note Title**", result)
        pb_mock.collection("notes").delete.assert_called_with("note_rec_1")

    @patch("cogs.notes.get_pb_client")
    @patch("cogs.notes.get_discord_user_id")
    async def test_delete_note_by_query(self, mock_get_user_id, mock_get_pb):
        from cogs.notes import Notes

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        # get_one fails (not an exact ID)
        pb_mock.collection.return_value.get_one.side_effect = Exception("Not found")
        note_record = MockRecord(id="note_rec_2", owner="pb_user_123", title="Grocery List", text="Apples and Oranges")
        pb_mock.collection.return_value.get_full_list.return_value = [note_record]

        bot_mock = MagicMock()
        cog = Notes(bot_mock)

        result = await cog.delete_note("discord_123", "Grocery")
        self.assertIn("Successfully deleted note: **Grocery List**", result)
        pb_mock.collection("notes").delete.assert_called_with("note_rec_2")

    @patch("cogs.notes.get_pb_client")
    @patch("cogs.notes.get_discord_user_id")
    async def test_delete_note_unlinked_user(self, mock_get_user_id, mock_get_pb):
        from cogs.notes import Notes

        mock_get_user_id.return_value = None
        bot_mock = MagicMock()
        cog = Notes(bot_mock)

        result = await cog.delete_note("discord_unlinked", "note_1")
        self.assertIn("Error: You have not linked your Discord account", result)

    @patch("cogs.notes.get_pb_client")
    @patch("cogs.notes.get_discord_user_id")
    async def test_delete_note_not_found(self, mock_get_user_id, mock_get_pb):
        from cogs.notes import Notes

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock
        pb_mock.collection.return_value.get_one.side_effect = Exception("Not found")
        pb_mock.collection.return_value.get_full_list.return_value = []

        bot_mock = MagicMock()
        cog = Notes(bot_mock)

        result = await cog.delete_note("discord_123", "Nonexistent")
        self.assertIn("No notes found matching 'Nonexistent'", result)

    # -------------------------------------------------------------
    # 2. Reading List Books Deletion Tests
    # -------------------------------------------------------------
    @patch("cogs.reading_list.get_discord_user_id")
    async def test_delete_book_by_id(self, mock_get_user_id):
        from cogs.reading_list import ReadingList

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        book_record = MockRecord(id="book_rec_1", owner="pb_user_123", title="Project Hail Mary", author="Andy Weir")
        pb_mock.collection.return_value.get_one.return_value = book_record

        bot_mock = MagicMock()
        cog = ReadingList(bot_mock)
        cog.get_pb_client = MagicMock(return_value=pb_mock)

        result = await cog.delete_book_from_pocketbase("discord_123", "book_rec_1")
        self.assertIn("Successfully removed **Project Hail Mary** by Andy Weir from your reading list.", result)
        pb_mock.collection("shisho_books").delete.assert_called_with("book_rec_1")

    @patch("cogs.reading_list.get_discord_user_id")
    async def test_delete_book_by_title_or_isbn(self, mock_get_user_id):
        from cogs.reading_list import ReadingList

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        pb_mock.collection.return_value.get_one.side_effect = Exception("Not found")
        book_record = MockRecord(id="book_rec_2", owner="pb_user_123", title="Dune", author="Frank Herbert", isbn="9780441172719")
        pb_mock.collection.return_value.get_full_list.return_value = [book_record]

        bot_mock = MagicMock()
        cog = ReadingList(bot_mock)
        cog.get_pb_client = MagicMock(return_value=pb_mock)

        result = await cog.delete_book_from_pocketbase("discord_123", "Dune")
        self.assertIn("Successfully removed **Dune** by Frank Herbert from your reading list.", result)
        pb_mock.collection("shisho_books").delete.assert_called_with("book_rec_2")

    # -------------------------------------------------------------
    # 3. Reminders Deletion Tests
    # -------------------------------------------------------------
    @patch("cogs.reminders.get_pb_client")
    @patch("cogs.reminders.get_discord_user_id")
    async def test_delete_reminder_by_id(self, mock_get_user_id, mock_get_pb):
        from cogs.reminders import Reminders

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock
        rem_record = MockRecord(id="rem_1", owner="pb_user_123", reminder_text="Take out trash", is_sent=False)
        pb_mock.collection.return_value.get_one.return_value = rem_record

        bot_mock = MagicMock()
        bot_mock.wait_until_ready = AsyncMock()
        cog = Reminders(bot_mock)
        self.addCleanup(cog.cog_unload)

        result = await cog.delete_reminder("discord_123", "rem_1")
        self.assertIn("Successfully deleted reminder: **Take out trash**", result)
        pb_mock.collection("reminders").delete.assert_called_with("rem_1")

    @patch("cogs.reminders.get_pb_client")
    @patch("cogs.reminders.get_discord_user_id")
    async def test_delete_reminder_by_index(self, mock_get_user_id, mock_get_pb):
        from cogs.reminders import Reminders

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock
        pb_mock.collection.return_value.get_one.side_effect = Exception("Not an ID")

        rem1 = MockRecord(id="rem_1", owner="pb_user_123", reminder_text="First Reminder", remind_at="2026-06-01 10:00:00.000Z", is_sent=False)
        rem2 = MockRecord(id="rem_2", owner="pb_user_123", reminder_text="Second Reminder", remind_at="2026-06-02 10:00:00.000Z", is_sent=False)
        pb_mock.collection.return_value.get_full_list.return_value = [rem1, rem2]

        bot_mock = MagicMock()
        bot_mock.wait_until_ready = AsyncMock()
        cog = Reminders(bot_mock)
        self.addCleanup(cog.cog_unload)

        result = await cog.delete_reminder("discord_123", "2")
        self.assertIn("Successfully deleted reminder #2: **Second Reminder**", result)
        pb_mock.collection("reminders").delete.assert_called_with("rem_2")

    @patch("cogs.reminders.get_pb_client")
    @patch("cogs.reminders.get_discord_user_id")
    async def test_delete_reminder_all(self, mock_get_user_id, mock_get_pb):
        from cogs.reminders import Reminders

        mock_get_user_id.return_value = "pb_user_123"
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        rem1 = MockRecord(id="rem_1", owner="pb_user_123", reminder_text="First", is_sent=False)
        rem2 = MockRecord(id="rem_2", owner="pb_user_123", reminder_text="Second", is_sent=False)
        pb_mock.collection.return_value.get_full_list.return_value = [rem1, rem2]

        bot_mock = MagicMock()
        bot_mock.wait_until_ready = AsyncMock()
        cog = Reminders(bot_mock)
        self.addCleanup(cog.cog_unload)

        result = await cog.delete_reminder("discord_123", "all")
        self.assertIn("Successfully deleted all (2) active reminder(s).", result)
        self.assertEqual(pb_mock.collection("reminders").delete.call_count, 2)

    # -------------------------------------------------------------
    # 4. Suggestions / Recommendations Deletion Tests
    # -------------------------------------------------------------
    @patch("cogs.suggested_books.get_pb_client")
    async def test_delete_suggestion_owner_or_author(self, mock_get_pb):
        from cogs.suggested_books import SuggestedBooks

        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        sug_record = MockRecord(
            id="sug_1",
            title="Neuromancer",
            sender_discord_id="111",
            recipient_discord_id="222",
            suggestedBy="alice#1234",
        )
        pb_mock.collection.return_value.get_one.return_value = sug_record

        bot_mock = MagicMock()
        cog = SuggestedBooks(bot_mock)

        # Sender (by discord id) can delete
        res1 = await cog.delete_suggestion("sug_1", user_discord_id="111", is_owner=False)
        self.assertIn("Successfully removed **Neuromancer**", res1)

        # Recipient (by discord id) can delete/dismiss
        res2 = await cog.delete_suggestion("sug_1", user_discord_id="222", is_owner=False)
        self.assertIn("Successfully removed **Neuromancer**", res2)

        # Unrelated user cannot delete
        res3 = await cog.delete_suggestion("sug_1", user_discord_id="333", is_owner=False)
        self.assertIn("You can only delete recommendations that you created or received.", res3)

        # Owner can delete anything
        res4 = await cog.delete_suggestion("sug_1", user_discord_id="333", is_owner=True)
        self.assertIn("Successfully removed **Neuromancer**", res4)

    @patch("cogs.suggested_books.get_discord_user_id")
    @patch("cogs.suggested_books.get_pb_client")
    async def test_add_suggestion_peer_to_peer(self, mock_get_pb, mock_get_user_id):
        from cogs.suggested_books import SuggestedBooks

        mock_get_user_id.side_effect = lambda pb, did: f"pb_{did}" if did else None
        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        bot_mock = MagicMock()
        cog = SuggestedBooks(bot_mock)

        res = await cog.add_suggestion(
            title="Klara and the Sun",
            author="Kazuo Ishiguro",
            sender_discord_id="111",
            recipient_discord_id="222",
            message="You will love this!",
            is_public=False,
        )

        self.assertEqual(res["title"], "Klara and the Sun")
        self.assertEqual(res["author"], "Kazuo Ishiguro")
        self.assertIn("**Klara and the Sun** by Kazuo Ishiguro", res["display_name"])

        pb_mock.collection.assert_called_with("shisho_books_recommendations")
        create_args = pb_mock.collection("shisho_books_recommendations").create.call_args[0][0]
        self.assertEqual(create_args["title"], "Klara and the Sun")
        self.assertEqual(create_args["author"], "Kazuo Ishiguro")
        self.assertEqual(create_args["sender"], "pb_111")
        self.assertEqual(create_args["sender_discord_id"], "111")
        self.assertEqual(create_args["recipient"], "pb_222")
        self.assertEqual(create_args["recipient_discord_id"], "222")
        self.assertEqual(create_args["message"], "You will love this!")
        self.assertFalse(create_args["is_public"])

    @patch("cogs.suggested_books.get_pb_client")
    async def test_get_suggestions_text_filtering(self, mock_get_pb):
        from cogs.suggested_books import SuggestedBooks

        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock

        rec1 = MockRecord(
            title="Book One",
            author="Author One",
            sender_discord_id="111",
            recipient_discord_id="222",
            is_public=False,
            message="Read this!",
            date_suggested="2026-08-30",
        )
        pb_mock.collection.return_value.get_list.return_value = MockRecord(items=[rec1])

        bot_mock = MagicMock()
        cog = SuggestedBooks(bot_mock)

        text = await cog.get_suggestions_text(user_discord_id="222", filter_type="for_me")
        self.assertIn("Books Recommended to You", text)
        self.assertIn("**Book One** by Author One", text)
        self.assertIn("From: <@111>", text)
        self.assertIn("To: <@222>", text)
        self.assertIn("Read this!", text)

    @patch("cogs.suggested_books.get_pb_client")
    async def test_delete_suggestion_by_search(self, mock_get_pb):
        from cogs.suggested_books import SuggestedBooks

        pb_mock = MagicMock()
        mock_get_pb.return_value = pb_mock
        # get_one fails
        pb_mock.collection.return_value.get_one.side_effect = Exception("Not an ID")

        rec1 = MockRecord(
            id="sug_search_1",
            title="Hyperion",
            author="Dan Simmons",
            sender_discord_id="111",
            recipient_discord_id="222",
            isbn="9780553283686",
        )
        pb_mock.collection.return_value.get_full_list.return_value = [rec1]

        bot_mock = MagicMock()
        cog = SuggestedBooks(bot_mock)

        res = await cog.delete_suggestion("Hyperion", user_discord_id="111", is_owner=False)
        self.assertIn("Successfully removed **Hyperion** from recommendations.", res)
        pb_mock.collection("shisho_books_recommendations").delete.assert_called_with("sug_search_1")

    # -------------------------------------------------------------
    # 5. Email Gateway Command Routing Tests
    # -------------------------------------------------------------
    async def test_email_gateway_delete_handlers(self):
        from cogs.email_gateway import EmailGateway

        bot_mock = MagicMock()
        reading_cog = MagicMock()
        reading_cog.delete_book_from_pocketbase = AsyncMock(return_value="Deleted book success")
        reminders_cog = MagicMock()
        reminders_cog.delete_reminder = AsyncMock(return_value="Deleted reminder success")
        notes_cog = MagicMock()
        notes_cog.delete_note = AsyncMock(return_value="Deleted note success")
        suggested_cog = MagicMock()
        suggested_cog.delete_suggestion = AsyncMock(return_value="Deleted suggestion success")

        def get_cog_side_effect(name):
            return {
                "ReadingList": reading_cog,
                "Reminders": reminders_cog,
                "Notes": notes_cog,
                "SuggestedBooks": suggested_cog,
            }.get(name)

        bot_mock.get_cog.side_effect = get_cog_side_effect
        gateway = EmailGateway(bot_mock)

        resp, _ = await gateway._handle_deletebook("Dune", "", "test@example.com", [])
        self.assertEqual(resp, "Deleted book success")

        resp, _ = await gateway._handle_deletereminder("1", "", "test@example.com", [])
        self.assertEqual(resp, "Deleted reminder success")

        resp, _ = await gateway._handle_deletenote("My Note", "", "test@example.com", [])
        self.assertEqual(resp, "Deleted note success")

        resp, _ = await gateway._handle_deletesuggestion("Neuromancer", "", "test@example.com", [])
        self.assertEqual(resp, "Deleted suggestion success")

        # Test help includes commands
        help_resp, _ = await gateway._handle_help("", "", "test@example.com", [])
        self.assertIn("!deletebook", help_resp)
        self.assertIn("!deletereminder", help_resp)
        self.assertIn("!deletenote", help_resp)
        self.assertIn("!deletesuggestion", help_resp)

    # -------------------------------------------------------------
    # 6. AI Chat Tool Execution Tests
    # -------------------------------------------------------------
    async def test_ai_chat_execute_delete_tools(self):
        from cogs.ai_chat import AIChat

        bot_mock = MagicMock()
        reading_cog = MagicMock()
        reading_cog.delete_book_from_pocketbase = AsyncMock(return_value="Removed book from reading list.")
        reminders_cog = MagicMock()
        reminders_cog.delete_reminder = AsyncMock(return_value="Deleted reminder.")
        notes_cog = MagicMock()
        notes_cog.delete_note = AsyncMock(return_value="Deleted note.")

        def get_cog_side_effect(name):
            return {
                "ReadingList": reading_cog,
                "Reminders": reminders_cog,
                "Notes": notes_cog,
            }.get(name)

        bot_mock.get_cog.side_effect = get_cog_side_effect
        chat_cog = AIChat(bot_mock)

        res1 = await chat_cog._execute_tool("delete_book", {"query": "Project Hail Mary"}, "discord_123")
        self.assertEqual(res1, "Removed book from reading list.")
        reading_cog.delete_book_from_pocketbase.assert_called_with("discord_123", "Project Hail Mary")

        res2 = await chat_cog._execute_tool("delete_reminder", {"query": "trash"}, "discord_123")
        self.assertEqual(res2, "Deleted reminder.")
        reminders_cog.delete_reminder.assert_called_with("discord_123", "trash")

        res3 = await chat_cog._execute_tool("delete_note", {"query": "Grocery"}, "discord_123")
        self.assertEqual(res3, "Deleted note.")
        notes_cog.delete_note.assert_called_with("discord_123", "Grocery")


if __name__ == "__main__":
    unittest.main()
