import tempfile
import unittest
from pathlib import Path
from scripts.showcase import seed
from backend.deployment_guard import capture, verify
from backend.service import Service
from backend.store import Store


class ShowcaseIsolationTests(unittest.TestCase):
    def test_seed_is_disposable_and_never_reopens_existing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)/'synthetic'
            ids=seed(data)
            service=Service(Store(data))
            self.assertEqual(len(service.store.state()['portfolios']),1)
            self.assertEqual(len(service.store.state()['instruments']),4)
            self.assertEqual(service.notes.list(ids['portfolio_id'],ids['instrument_id'])['total'],2)
            before=capture(data)
            with self.assertRaises(FileExistsError):seed(data)
            verify(before,capture(data,before))
            link=Path(temp)/'alias';link.symlink_to(data,target_is_directory=True)
            with self.assertRaises(FileExistsError):seed(link)
            verify(before,capture(data,before))


if __name__=='__main__':unittest.main()
