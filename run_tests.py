import unittest
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
TESTS_DIR = str(CURRENT_DIR / "tests")

if __name__ == "__main__":
    print("=" * 60)
    print("EJECUTANDO SUITE DE PRUEBAS UNITARIAS - OCR SELLOS LIBROS")
    print("=" * 60)
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--unit":
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_folder_parser.py"))
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_text_normalizer.py"))
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_stamp_parser.py"))
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_comparator.py"))
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_sqlite_and_rollback.py"))
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_mysql_and_folder_tools.py"))
    else:
        # Por defecto ejecuta todos incluyendo integración si existe la ruta
        suite.addTests(loader.discover(TESTS_DIR, pattern="test_*.py"))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    sys.exit(0 if result.wasSuccessful() else 1)
