import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from rag_knowledge.services.document_support import (
    classify_suffix,
    make_decision,
    IngestionDecisionStore,
    IngestionDecision,
)


class DocumentSupportTests(TestCase):
    def test_classification_matrix(self):
        # Supported format actions
        self.assertEqual(classify_suffix(".docx").action, "process")
        self.assertEqual(classify_suffix(".pdf").action, "process")
        self.assertEqual(classify_suffix(".pptx").action, "process")
        self.assertEqual(classify_suffix(".xml").action, "process")
        self.assertEqual(classify_suffix(".html").action, "process")
        self.assertEqual(classify_suffix(".htm").action, "process")
        self.assertEqual(classify_suffix(".sql").action, "process")
        self.assertEqual(classify_suffix(".ini").action, "process")

        # Disabled by config (enabled_extensions overrides)
        self.assertEqual(
            classify_suffix(".pdf", enabled_extensions={".docx"}).action,
            "excluded",
        )
        self.assertEqual(
            classify_suffix(".pdf", enabled_extensions={".docx"}).reason_code,
            "DISABLED_BY_CONFIG",
        )

        # Queued format actions
        self.assertEqual(classify_suffix(".doc").action, "queued")
        self.assertEqual(
            classify_suffix(".doc").reason_code, "LEGACY_DOC_REQUIRES_CONVERSION"
        )
        self.assertEqual(classify_suffix(".xls").action, "queued")
        self.assertEqual(
            classify_suffix(".xls").reason_code,
            "LEGACY_SPREADSHEET_REQUIRES_CONVERSION",
        )
        self.assertEqual(classify_suffix(".png").action, "queued")
        self.assertEqual(
            classify_suffix(".png").reason_code, "MEDIA_PROCESSING_DEFERRED"
        )

        # Excluded format actions
        self.assertEqual(classify_suffix(".jar").action, "excluded")
        self.assertEqual(classify_suffix(".jar").reason_code, "DEPENDENCY_ASSET")
        self.assertEqual(classify_suffix(".zip").action, "excluded")
        self.assertEqual(classify_suffix(".zip").reason_code, "ARCHIVE_ASSET")
        self.assertEqual(classify_suffix(".unknown").action, "excluded")
        self.assertEqual(
            classify_suffix(".unknown").reason_code, "UNSUPPORTED_EXTENSION"
        )

    def test_decision_persistence_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "ingestion_decisions.json"
            store = IngestionDecisionStore(store_path)

            # Test initial state
            self.assertEqual(store.snapshot()["decisions"], {})

            # Make decisions
            file1 = Path("watch_dir/doc1.pdf")
            dec1 = make_decision(
                file1,
                status="queued",
                reason_code="PDF_PAGE_REQUIRES_OCR",
                file_hash="hash1",
                locator="page:1",
            )
            dec2 = make_decision(
                file1,
                status="queued",
                reason_code="EMBEDDED_MEDIA_PROCESSING_DEFERRED",
                file_hash="hash1",
                locator="page:2",
            )

            # Replace/Save
            store.replace_for_file(
                file_path=str(file1), file_hash="hash1", decisions=[dec1, dec2]
            )

            # Reload and check
            store.reload()
            decisions = store.snapshot()["decisions"]
            self.assertEqual(len(decisions), 2)

            # Relocation (file moved)
            file1_moved = Path("watch_dir/sub/doc1.pdf")
            store.relocate(file_hash="hash1", file_path=str(file1_moved))
            store.reload()
            decisions_moved = list(store.snapshot()["decisions"].values())
            self.assertEqual(len(decisions_moved), 2)
            self.assertTrue(
                all(d["file_path"] == str(file1_moved) for d in decisions_moved)
            )

            # Pruning/Delete
            # Create the actual file so it's not pruned yet
            base = Path(tmpdir)
            (base / file1_moved).parent.mkdir(parents=True, exist_ok=True)
            (base / file1_moved).write_text("text")

            # Pruning when file exists should do nothing
            store.prune_missing(base)
            self.assertEqual(len(store.snapshot()["decisions"]), 2)

            # Remove file and prune
            (base / file1_moved).unlink()
            store.prune_missing(base)
            self.assertEqual(len(store.snapshot()["decisions"]), 0)

    def test_corrupted_json_recovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "corrupted.json"
            store_path.write_text("invalid json {", encoding="utf-8")

            # Load should safely recover to empty structure
            store = IngestionDecisionStore(store_path)
            self.assertEqual(store.snapshot()["version"], 1)
            self.assertEqual(store.snapshot()["decisions"], {})

    def test_unchanged_decisions_do_not_rewrite_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IngestionDecisionStore(Path(tmpdir) / "ingestion_decisions.json")
            decision = make_decision(
                "watch_dir/image.png",
                status="queued",
                reason_code="MEDIA_PROCESSING_DEFERRED",
                file_hash="hash1",
            )
            store.replace_for_file(
                file_path="watch_dir/image.png", file_hash="hash1", decisions=[decision]
            )

            with patch.object(store, "save") as save:
                store.replace_for_file(
                    file_path="watch_dir/image.png",
                    file_hash="hash1",
                    decisions=[
                        make_decision(
                            "watch_dir/image.png",
                            status="queued",
                            reason_code="MEDIA_PROCESSING_DEFERRED",
                            file_hash="hash1",
                        )
                    ],
                )

            save.assert_not_called()

    def test_store_retries_replace_when_target_is_temporarily_locked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = IngestionDecisionStore(Path(tmpdir) / "ingestion_decisions.json")
            real_replace = os.replace
            attempts = 0

            def replace_after_transient_lock(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("temporarily locked")
                return real_replace(source, target)

            with patch(
                "rag_knowledge.services.document_support.os.replace",
                side_effect=replace_after_transient_lock,
            ):
                store.save()

            self.assertEqual(attempts, 3)
            self.assertTrue(store.path.exists())
