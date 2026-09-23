from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PythonFileLengthTests(unittest.TestCase):
    def test_all_python_files_stay_below_1000_physical_lines(self):
        offenders = []
        for path in ROOT.rglob("*.py"):
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            count = len(path.read_text(encoding="utf-8").splitlines())
            if count >= 1000:
                offenders.append(f"{path.relative_to(ROOT)}: {count}")
        self.assertEqual(offenders, [], "Python files must stay below 1000 lines")

if __name__ == "__main__":
    unittest.main()
