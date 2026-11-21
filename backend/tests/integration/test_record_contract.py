import pytest

from support.record_contract import RecordStoreContract

pytestmark = pytest.mark.integration


class TestPostgresRecordContract(RecordStoreContract):
    @pytest.fixture
    def store(self, pg):
        with pg.platform.uow(pg.admin.organization_id) as tx:
            yield tx.records
