import unittest
from unittest.mock import patch

from importlib import metadata

from rag_knowledge.runtime_guard import validate_chroma_runtime


class RuntimeGuardTests(unittest.TestCase):
    @patch("rag_knowledge.runtime_guard.metadata.version")
    def test_matching_versions_pass(self, version):
        version.side_effect = lambda package: {
            "chromadb": "0.6.3",
            "langchain-chroma": "0.2.3",
        }[package]

        self.assertEqual(
            validate_chroma_runtime(),
            {"chromadb": "0.6.3", "langchain-chroma": "0.2.3"},
        )

    @patch("rag_knowledge.runtime_guard.metadata.version")
    def test_wrong_version_fails_with_actionable_message(self, version):
        version.side_effect = lambda package: {
            "chromadb": "1.5.9",
            "langchain-chroma": "1.1.0",
        }[package]

        with self.assertRaisesRegex(RuntimeError, r"chromadb: 需要 0\.6\.3.*venv"):
            validate_chroma_runtime()

    @patch("rag_knowledge.runtime_guard.metadata.version")
    def test_missing_package_fails(self, version):
        def lookup(package):
            if package == "chromadb":
                raise metadata.PackageNotFoundError(package)
            return "0.2.3"

        version.side_effect = lookup
        with self.assertRaisesRegex(RuntimeError, "chromadb: 需要 0.6.3，当前 未安装"):
            validate_chroma_runtime()


if __name__ == "__main__":
    unittest.main()
