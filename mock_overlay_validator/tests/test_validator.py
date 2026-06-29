import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import validate as V


def _codes(report):
    return {(i.severity, i.code) for i in report.issues}


def _has_error(report, code):
    return any(i.severity == V.SEV_ERROR and i.code == code for i in report.issues)


def _has_warn(report, code):
    return any(i.severity == V.SEV_WARN and i.code == code for i in report.issues)


class CatalogTests(unittest.TestCase):
    def test_examples_cover_all_known_apis(self):
        apis = V.list_apis()
        self.assertGreaterEqual(len(apis), 100)
        for api in apis:
            self.assertTrue(
                (V.EXAMPLES_DIR / api).is_dir(),
                f"{api}: examples dir missing",
            )
            files = V.example_files_for_api(api)
            self.assertTrue(files, f"{api}: no example files")

    def test_gmail_example_lists_messages(self):
        files = V.example_files_for_api("gmail-api")
        self.assertIn("messages", files)


class HappyPathTests(unittest.TestCase):
    def test_baseline_example_validates_against_itself(self):
        ex = V.EXAMPLES_DIR / "gmail-api" / "messages.json"
        report = V.Report()
        V.validate_file(ex, "gmail-api", report)
        self.assertEqual(len(report.errors), 0, msg=str(report.issues))


class StructuralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _gmail_overlay(self, name, body):
        p = self.dir / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_duplicate_header_is_error(self):
        p = self._gmail_overlay("messages.csv", "id,id\nA,B\n")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_error(report, "CSV_DUPLICATE_HEADER"))

    def test_ragged_row_is_error(self):
        p = self._gmail_overlay("messages.csv", "id,subject\nA,B,C\n")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_error(report, "CSV_RAGGED_ROW"))

    def test_malformed_json_is_error(self):
        p = self._gmail_overlay("messages.json", "{not json")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_error(report, "JSON_MALFORMED"))

    def test_json_object_for_table_is_error(self):
        p = self._gmail_overlay("messages.json", '{"id": "A"}')
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_error(report, "JSON_NOT_ARRAY"))

    def test_unknown_extension_is_warn(self):
        p = self._gmail_overlay("messages.txt", "hi")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_warn(report, "UNKNOWN_EXTENSION"))


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_missing_column_is_error(self):
        ex = V.EXAMPLES_DIR / "gmail-api" / "messages.json"
        ex_rows = json.loads(ex.read_text())
        cols = list(ex_rows[0].keys())
        drop = cols[-1]
        keep = [c for c in cols if c != drop]
        body = ",".join(keep) + "\n"
        body += ",".join("x" for _ in keep) + "\n"
        p = self.dir / "messages.csv"
        p.write_text(body, encoding="utf-8")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_error(report, "SCHEMA_MISSING_COLUMNS"))

    def test_extra_column_is_warn(self):
        ex = V.EXAMPLES_DIR / "gmail-api" / "messages.json"
        ex_rows = json.loads(ex.read_text())
        cols = list(ex_rows[0].keys()) + ["totally_made_up_column"]
        body = ",".join(cols) + "\n" + ",".join("x" for _ in cols) + "\n"
        p = self.dir / "messages.csv"
        p.write_text(body, encoding="utf-8")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_warn(report, "SCHEMA_EXTRA_COLUMNS"))

    def test_unregistered_filename_is_warn(self):
        p = self.dir / "totally_unknown.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")
        report = V.Report()
        V.validate_file(p, "gmail-api", report)
        self.assertTrue(_has_warn(report, "UNREGISTERED_FILENAME"))

    def test_unknown_api_is_error(self):
        p = self.dir / "messages.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")
        report = V.Report()
        V.validate_file(p, "no-such-api", report)
        self.assertTrue(_has_error(report, "UNKNOWN_API"))


class WrappedTableTests(unittest.TestCase):
    def test_quickbooks_customers_treated_as_table(self):
        ex = V.EXAMPLES_DIR / "quickbooks-api" / "customers.json"
        self.assertFalse(V._example_is_document(ex))
        rows, issues = V._load_table(ex)
        self.assertIsNotNone(rows)
        self.assertGreater(len(rows), 0)


class ReportShapeTests(unittest.TestCase):
    def test_json_serialisation(self):
        report = V.Report()
        report.add(V.Issue(severity=V.SEV_ERROR, code="X", message="m"))
        data = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(data["summary"]["errors"], 1)
        self.assertEqual(data["issues"][0]["code"], "X")


if __name__ == "__main__":
    unittest.main()
