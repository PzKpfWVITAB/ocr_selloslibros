import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

from src.database.mysql_manager import MySQLManager
from src.service import FolderOCRService, OperationBusyError


def _pdf(path: Path):
    d = fitz.open()
    d.new_page()
    d.save(str(path))
    d.close()


class TestRollbackRobustness(unittest.TestCase):
    DB = "test_rb_robustness_selloslibros"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ocr_rb_"))
        self.root = self.tmp / "raiz"
        self.root.mkdir()
        self.cfg = {
            "mysql_enabled": True, "mysql_host": "127.0.0.1", "mysql_port": 3306,
            "mysql_user": "root", "mysql_password": "", "mysql_database": self.DB,
        }
        self.patcher = patch("src.service.SettingsManager.load", return_value=dict(self.cfg))
        self.patcher.start()
        m = MySQLManager(self.cfg)
        ok, msg = m.init_database_and_tables()
        self.assertTrue(ok, msg)
        m.reset_database()
        self.m = m

    def tearDown(self):
        try:
            self.patcher.stop()
        except Exception:
            pass
        try:
            c = self.m.get_connection(include_db=False)
            c.cursor().execute(f"DROP DATABASE IF EXISTS `{self.DB}`")
            c.commit()
            c.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _svc(self):
        return FolderOCRService()

    def test_collision_chain_rolls_back_completely(self):
        # A (nombre viejo) y B (nombre ya correcto "X"): A -> X_2 en paso 1; rollback debe devolver ambos.
        x = "01_01_02_00_00_00_1995"
        a = self.root / "01_01_02_00_00_00_1995_ZZ"
        b = self.root / x
        a.mkdir(); b.mkdir()
        _pdf(a / f"{x}_0001_0010.pdf")
        _pdf(b / f"{x}_0011_0020.pdf")
        svc = self._svc()
        res = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        self.assertEqual(res["errores"], [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted([x, x + "_2"]))
        rb = svc.ejecutar_rollback_ultima_ejecucion()
        self.assertTrue(rb["success"], rb["errors"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted([a.name, x]))

    def test_rerun_step1_does_not_duplicate_index(self):
        a = self.root / "01_01_03_00_00_00_1996_OLD"
        a.mkdir()
        _pdf(a / "01_01_03_00_00_00_1996_0001_0010.pdf")
        svc = self._svc()
        r1 = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        r2 = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        self.assertEqual(r1["nuevas_indexadas"], 1)
        self.assertEqual(r2["nuevas_indexadas"], 0)  # misma carpeta, NO una nueva
        v = svc.mysql.verificar_invariante([(str(self.root), 1)])
        self.assertEqual(v["total_indexadas"], 1)
        self.assertTrue(v["congruente"], v)

    def test_batch_with_errors_is_still_revertible(self):
        a = self.root / "01_01_04_00_00_00_1997_OLD"
        a.mkdir()
        _pdf(a / "01_01_04_00_00_00_1997_0001_0010.pdf")
        svc = self._svc()
        res = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        with svc.mysql.get_connection(include_db=True) as _c:
            pass
        c = svc.mysql.get_connection(include_db=True)
        c.cursor().execute("UPDATE ejecuciones_lotes SET estado='COMPLETADO_CON_ERRORES' WHERE id=%s", (res["ejecucion_id"],))
        c.close()
        last = svc.mysql.obtener_ultima_ejecucion_revertible()
        self.assertEqual(last["id"], res["ejecucion_id"])
        self.assertTrue(svc.ejecutar_rollback_ultima_ejecucion()["success"])
        self.assertTrue(a.exists())

    def test_rollback_is_idempotent_and_partial_state(self):
        a = self.root / "01_01_05_00_00_00_1998_OLD"
        a.mkdir()
        _pdf(a / "01_01_05_00_00_00_1998_0001_0010.pdf")
        svc = self._svc()
        svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        # alguien ocupa el nombre original -> conflicto real
        (self.root / a.name).mkdir()
        rb = svc.ejecutar_rollback_ultima_ejecucion()
        self.assertFalse(rb["success"])
        self.assertTrue(svc.mysql.obtener_ultima_ejecucion_revertible())  # sigue pendiente
        (self.root / a.name).rmdir()
        rb2 = svc.ejecutar_rollback_ultima_ejecucion()
        self.assertTrue(rb2["success"], rb2["errors"])
        self.assertTrue(a.exists())

    def test_only_one_operation_at_a_time(self):
        a = self.root / "01_01_06_00_00_00_1999_OLD"
        a.mkdir()
        _pdf(a / "01_01_06_00_00_00_1999_0001_0010.pdf")
        svc = self._svc()
        inside, release, errs = threading.Event(), threading.Event(), []

        def slow(root, progress_cb=None):
            inside.set()
            release.wait(5)

        # Simula una operación larga sosteniendo el candado desde otro hilo
        from src.service import exclusive_op
        svc.slow = exclusive_op("lenta")(lambda self_: (inside.set(), release.wait(5)))
        t = threading.Thread(target=lambda: svc.slow(svc))
        t.start()
        inside.wait(2)
        with self.assertRaises(OperationBusyError):
            svc.ejecutar_rollback_ultima_ejecucion()
        release.set()
        t.join()

    def test_plan_matches_execution(self):
        a = self.root / "01_01_07_00_00_00_2000_OLD"
        a.mkdir()
        _pdf(a / "01_01_07_00_00_00_2000_0001_0010.pdf")
        sin_anio = self.root / "01_01_08_xx"
        sin_anio.mkdir()
        _pdf(sin_anio / "REG_60.pdf")
        svc = self._svc()
        plan = svc.planificar_paso1(str(self.root))
        self.assertEqual(plan["renombrar"], 1)
        self.assertEqual(plan["omitir"], 1)
        res = svc.ejecutar_paso1_indexar_y_renombrar(str(self.root))
        self.assertEqual(res["renombradas"], plan["renombrar"])
        self.assertEqual(len(res["omitidas"]), plan["omitir"])
        self.assertTrue(sin_anio.exists())  # omitida: intacta


if __name__ == "__main__":
    unittest.main()
