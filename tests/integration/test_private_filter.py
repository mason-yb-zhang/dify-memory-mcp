import unittest

import server


class PrivateFilterTests(unittest.TestCase):
    def test_removes_private_block(self):
        result = server._prepare_memory_content("alpha <private>secret</private> beta")

        self.assertEqual(result, "alpha beta")

    def test_removes_multiple_private_blocks(self):
        result = server._prepare_memory_content(
            "a <private>one</private> b <private>two</private> c"
        )

        self.assertEqual(result, "a b c")

    def test_removes_unclosed_private_block_to_end(self):
        result = server._prepare_memory_content("visible <private>secret")

        self.assertEqual(result, "visible")

    def test_private_only_content_is_skipped(self):
        result = server._prepare_memory_content("<private>secret</private>")

        self.assertIsNone(result)


class PrivateFilterWritePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_memory_skips_private_only_content(self):
        result = await server.add_memory("<private>secret</private>")

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])

    async def test_add_memory_sends_sanitized_content(self):
        calls = []
        original = server.mify_request

        async def fake_mify_request(method, path, body=None):
            calls.append((method, path, body))
            return {
                "data": [
                    {
                        "id": "seg-1",
                        "content": body["segments"][0]["content"],
                        "status": "completed",
                    }
                ]
            }

        server.mify_request = fake_mify_request
        try:
            result = await server.add_memory("alpha <private>secret</private> beta")
        finally:
            server.mify_request = original

        self.assertEqual(result["content"], "alpha beta")
        self.assertEqual(calls[0][2]["segments"][0]["content"], "alpha beta")

    async def test_update_memory_skips_private_only_content(self):
        result = await server.update_memory("seg-1", "<private>secret</private>")

        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["segment_id"], "seg-1")


if __name__ == "__main__":
    unittest.main()
