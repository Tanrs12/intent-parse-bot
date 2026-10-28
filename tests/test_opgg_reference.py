import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from league_opgg_reference import reference_signal

DRAFT={"Top":"Ornn","Jungle":"Vi","Mid":"Orianna","ADC":"Jinx","Support":"Lulu"}

class OpggReferenceTests(unittest.TestCase):
    def _write(self, path, *, age=0, patch=None):
        roles={}
        for role, champion in DRAFT.items():
            group={f"Test {i}":{"games":8000,"win_rate":.50,"tier":3}
                   for i in range(8)}
            group[champion]={"games":10000,"win_rate":.54,"tier":2}
            roles[role]=group
        data={"source":"OP.GG MCP","provenance":"unversioned-public-lane-meta",
              "captured_at":(datetime.now(timezone.utc)-timedelta(hours=age)).isoformat(),
              "patch":patch,"rank":"unspecified","roles":roles}
        path.write_text(json.dumps(data),encoding="utf-8")
    def test_recent_unversioned_can_only_nudge(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"test.json"
            self._write(path)
            result=reference_signal(DRAFT,path=path)
            self.assertGreater(result["adjustment"],0)
            self.assertLessEqual(abs(result["adjustment"]),.35)
            self.assertEqual(result["covered_roles"],5)
            self.assertIsNone(result["patch"])
    def test_old_cache_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"test.json"
            self._write(path,age=49)
            self.assertEqual(reference_signal(DRAFT,path=path)["adjustment"],0)
    def test_mislabelled_patch_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"test.json"
            self._write(path,patch="26.21")
            self.assertEqual(reference_signal(DRAFT,path=path)["covered_roles"],0)
    def test_missing_cache_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(reference_signal(DRAFT,path=Path(td)/"none.json")["adjustment"],0)
if __name__=="__main__":
    unittest.main()
