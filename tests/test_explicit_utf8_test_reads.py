import ast
from pathlib import Path
import unittest


class ExplicitUtf8TextTests(unittest.TestCase):
    def test_all_test_read_text_calls_set_encoding(self):
        root = Path(__file__).resolve().parent
        missing = []
        for file in root.rglob("*.py"):
            source = file.read_text(encoding="utf-8")
            parsed = ast.parse(source, filename=str(file))
            for node in ast.walk(parsed):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_text"
                    and not node.args
                    and not any(keyword.arg == "encoding" for keyword in node.keywords)
                ):
                    missing.append(f"{file.relative_to(root)}:{node.lineno}")
        self.assertEqual([], missing, f"read_text needs an explicit UTF-8 encoding: {missing[:20]}")


if __name__ == "__main__":
    unittest.main()
