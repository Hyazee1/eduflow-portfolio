import pytest

from support.fakes import MemoryRecords
from support.record_contract import RecordStoreContract


class TestMemoryRecordContract(RecordStoreContract):
    @pytest.fixture
    def store(self, legal_week):
        return MemoryRecords(legal_week[0])
