import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import pymysql

from src.core.file_operations import (
    FileOperations,
    safe_replace,
    safe_move,
    normalize_win32_path,
)
from src.database.mysql_manager import MySQLManager


class TestSoftwareQualityAudit(unittest.TestCase):
    """
    Suite de Pruebas de Calidad de Software, Manejo Transaccional de Errores,
    Tolerancia a Fallos, Compensación Física (Rollback Inmediato) y Cero Pérdida de Datos.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)

    def tearDown(self):
        if Path(self.test_dir).exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_dummy_pdf(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Dummy PDF header bytes
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj\n<<\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF\n")

    def test_safe_replace_retries_and_succeeds(self):
        """Valida que safe_replace reintente ante un bloqueo transitorio (WinError 32) y tenga éxito."""
        src = self.test_path / "src.txt"
        dst = self.test_path / "dst.txt"
        src.write_text("contenido prueba")

        attempts = [0]
        orig_os_replace = os.replace

        def mock_replace(s, d):
            attempts[0] += 1
            if attempts[0] < 2:
                raise PermissionError("[WinError 32] El archivo está siendo utilizado por otro proceso.")
            return orig_os_replace(s, d)

        with patch("os.replace", side_effect=mock_replace):
            safe_replace(src, dst, max_retries=3, delay=0.01)

        self.assertFalse(src.exists())
        self.assertTrue(dst.exists())
        self.assertEqual(dst.read_text(), "contenido prueba")
        self.assertEqual(attempts[0], 2)

    def test_atomic_pdf_rename_with_compensatory_rollback(self):
        """
        Valida que si falla el renombrado del segundo o tercer PDF dentro de una carpeta,
        los PDFs previamente renombrados se restauren atómicamente a su nombre original.
        """
        folder = self.test_path / "01_00_00_00_00_00_0000"
        folder.mkdir()
        pdf1 = folder / "01_00_00_00_00_00_0000_0001_00.pdf"
        pdf2 = folder / "01_00_00_00_00_00_0000_0002_00.pdf"
        pdf3 = folder / "01_00_00_00_00_00_0000_CARATULA_00.pdf"
        self._create_dummy_pdf(pdf1)
        self._create_dummy_pdf(pdf2)
        self._create_dummy_pdf(pdf3)

        orig_replace = os.replace
        call_count = [0]

        def failing_replace(src, dst):
            call_count[0] += 1
            # Falla en el segundo PDF que intente renombrar
            if "0002_00.pdf" in str(dst):
                raise PermissionError("Bloqueo de archivo simulado en PDF 2")
            return orig_replace(src, dst)

        with patch("src.core.file_operations.safe_replace", side_effect=failing_replace):
            with self.assertRaises(RuntimeError) as ctx:
                FileOperations.rename_folder_and_internal_pdfs(
                    folder_path=folder,
                    clean_base_code="01_01_01_00_00_00_1995",
                )
            self.assertIn("Error al renombrar PDFs", str(ctx.exception))

        # Comprobación de integridad: todos los archivos deben haber regresado a su nombre original
        self.assertTrue(pdf1.exists(), "El PDF 1 debe haber sido restaurado a su nombre original")
        self.assertTrue(pdf2.exists(), "El PDF 2 debe conservar su nombre original")
        self.assertTrue(pdf3.exists(), "La carátula debe conservar su nombre original")
        self.assertTrue(folder.exists(), "La carpeta debe permanecer en su ruta original")

    def test_atomic_folder_rename_mysql_failure_compensatory_rollback(self):
        """
        Valida que si todos los archivos y la carpeta se renombran físicamente, pero el registro
        en MySQL falla, se ejecute una reversión compensatoria que devuelva tanto la carpeta
        como los PDFs a su nombre original exacto.
        """
        folder = self.test_path / "01_00_00_00_00_00_0000"
        folder.mkdir()
        pdf1 = folder / "01_00_00_00_00_00_0000_0001_00.pdf"
        self._create_dummy_pdf(pdf1)

        mock_mysql = MagicMock()
        mock_mysql.enabled = True
        mock_mysql.obtener_uuid_por_ruta_o_nombre.return_value = "dummy-uuid-1234"
        mock_mysql.registrar_movimiento_carpeta.side_effect = pymysql.err.OperationalError(
            2006, "MySQL server has gone away (simulado)"
        )

        with self.assertRaises(RuntimeError) as ctx:
            FileOperations.rename_folder_and_internal_pdfs(
                folder_path=folder,
                clean_base_code="01_02_03_00_00_00_2005",
                mysql_manager=mock_mysql,
                ejecucion_id=99,
            )

        self.assertIn("Fallo al registrar trazabilidad en MySQL", str(ctx.exception))
        self.assertIn("reversión física compensatoria", str(ctx.exception))

        # Integridad física: la carpeta y el archivo deben estar intactos en su nombre original
        self.assertTrue(folder.exists(), "La carpeta debe haber sido devuelta a su nombre original")
        self.assertTrue(pdf1.exists(), "El archivo PDF debe haber sido devuelto a su nombre original")
        target_folder = self.test_path / "01_02_03_00_00_00_2005"
        self.assertFalse(target_folder.exists(), "La carpeta con nuevo nombre no debe quedar en disco")

    def test_atomic_move_to_apartados_compensatory_rollback(self):
        """
        Valida que si una carpeta se mueve físicamente a Apartados, pero falla el registro
        en MySQL, la carpeta se devuelva a su ubicación y nombre original de inmediato,
        y se limpie la subcarpeta creada si quedó vacía.
        """
        root_dir = self.test_path / "RAIZ"
        apartados_dir = self.test_path / "APARTADOS"
        root_dir.mkdir()
        apartados_dir.mkdir()

        folder = root_dir / "01_00_00_00_00_00_0000"
        folder.mkdir()
        pdf1 = folder / "01_00_00_00_00_00_0000_CARATULA_00.pdf"
        self._create_dummy_pdf(pdf1)

        mock_mysql = MagicMock()
        mock_mysql.enabled = True
        mock_mysql.obtener_uuid_por_ruta_o_nombre.return_value = "dummy-uuid-apartado"
        mock_mysql.registrar_movimiento_carpeta.side_effect = pymysql.err.OperationalError(
            1040, "Too many connections (simulado)"
        )

        with self.assertRaises(RuntimeError) as ctx:
            FileOperations.move_folder_to_apartados(
                folder_path=folder,
                apartados_dir=apartados_dir,
                error_subfolder="CARATULA EN BLANCO",
                mysql_manager=mock_mysql,
                ejecucion_id=101,
            )

        self.assertIn("Se devolvió la carpeta a su ubicación original", str(ctx.exception))
        self.assertTrue(folder.exists(), "La carpeta debe existir en la raíz original")
        self.assertTrue(pdf1.exists(), "El PDF debe estar intacto en la raíz original")

        subfolder = apartados_dir / "CARATULA EN BLANCO"
        self.assertFalse(subfolder.exists(), "La subcarpeta vacía creada debe haberse limpiado")

    def test_mysql_get_connection_retry_resilience(self):
        """Valida que get_connection reintente con backoff ante caídas transitorias de red/daemon."""
        mgr = MySQLManager({
            "mysql_host": "127.0.0.1",
            "mysql_port": 3306,
            "mysql_user": "root",
            "mysql_password": "",
            "mysql_database": "test_db",
        })

        attempts = [0]
        mock_conn = MagicMock()

        def mock_pymysql_connect(*args, **kwargs):
            attempts[0] += 1
            if attempts[0] < 3:
                raise pymysql.err.OperationalError(2003, "Can't connect to MySQL server on '127.0.0.1'")
            return mock_conn

        with patch("pymysql.connect", side_effect=mock_pymysql_connect):
            conn = mgr.get_connection(max_retries=3, retry_delay=0.01)
            self.assertEqual(conn, mock_conn)
            self.assertEqual(attempts[0], 3)

    def test_no_silent_exceptions_in_stats_and_traceability(self):
        """Valida que get_executive_statistics y obtener_matriz_trazabilidad capturen y registren errores sin crasheos mudos."""
        mgr = MySQLManager()
        with patch.object(mgr, "get_connection", side_effect=Exception("Fallo forzado de MySQL")):
            stats = mgr.get_executive_statistics()
            self.assertIsInstance(stats, dict)
            self.assertIn("total_carpetas_iniciales", stats)

            items = mgr.obtener_matriz_trazabilidad(modo="carpetas")
            self.assertEqual(items, [])

    def test_normalize_win32_path(self):
        """Valida la compatibilidad con rutas extendidas y UNC."""
        short_p = Path("C:/test/file.txt")
        self.assertEqual(normalize_win32_path(short_p), short_p)

        if os.name == "nt":
            long_path_str = "C:\\" + ("a" * 250) + "\\archivo.pdf"
            norm = normalize_win32_path(Path(long_path_str))
            self.assertTrue(str(norm).startswith("\\\\?\\"))


if __name__ == "__main__":
    unittest.main()
