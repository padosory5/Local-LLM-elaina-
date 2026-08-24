import unittest

from tools.tool_registry import ToolRegistry, ToolResult, ToolSpec


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_get_round_trips_the_spec(self):
        registry = ToolRegistry()
        spec = ToolSpec(id="web.search", description="Search the web.")

        registry.register(spec, backend=lambda query: query)

        self.assertIs(registry.get("web.search"), spec)
        self.assertIsNone(registry.get("unknown.tool"))

    def test_to_ollama_tools_produces_the_standard_function_shape(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                id="web.search",
                description="Search the web.",
                required_inputs={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            backend=lambda query: query,
        )

        tools = registry.to_ollama_tools(("web.search",))

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "web.search")
        self.assertEqual(
            tools[0]["function"]["parameters"]["required"], ["query"],
        )

    def test_to_ollama_tools_skips_unregistered_ids(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(id="web.search", description="Search the web."),
            backend=lambda query: query,
        )

        tools = registry.to_ollama_tools(("web.search", "memory.search"))

        self.assertEqual(len(tools), 1)

    def test_invoke_calls_the_bound_backend_with_keyword_arguments(self):
        registry = ToolRegistry()
        calls = []

        def backend(query):
            calls.append(query)
            return f"results for {query}"

        registry.register(
            ToolSpec(id="web.search", description="Search the web."),
            backend=backend,
        )

        result = registry.invoke("web.search", {"query": "hotels in Guam"})

        self.assertEqual(calls, ["hotels in Guam"])
        self.assertEqual(result.status, "done")
        self.assertEqual(result.data, "results for hotels in Guam")

    def test_invoke_unknown_tool_returns_a_structured_error(self):
        registry = ToolRegistry()

        result = registry.invoke("unknown.tool", {})

        self.assertEqual(result.status, "unknown_tool")
        self.assertIn("unknown.tool", result.message)

    def test_invoke_wraps_a_backend_exception_as_a_failed_result(self):
        registry = ToolRegistry()

        def backend():
            raise ValueError("boom")

        registry.register(
            ToolSpec(id="broken.tool", description="Always fails."),
            backend=backend,
        )

        result = registry.invoke("broken.tool")

        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.message)

    def test_invoke_passes_through_a_backend_returned_tool_result(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(id="custom.tool", description="Returns its own result."),
            backend=lambda: ToolResult("custom_status", message="ok"),
        )

        result = registry.invoke("custom.tool")

        self.assertEqual(result.status, "custom_status")
        self.assertEqual(result.message, "ok")


if __name__ == "__main__":
    unittest.main()
