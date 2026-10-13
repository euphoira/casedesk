import pytest

from casedesk.config import Settings
from casedesk.engine import Engine
from casedesk.retrieval import KnowledgeBase
from casedesk.store import Store


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path)
    store = Store(tmp_path / "test.sqlite3")
    knowledge = KnowledgeBase()
    return settings, store, knowledge, Engine(settings, store, knowledge)
