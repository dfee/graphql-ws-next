from unittest.mock import Mock

from aiohttp import web
import pytest


@pytest.fixture
def schema():
    return Mock()


@pytest.fixture
def context_value():
    return Mock()


@pytest.fixture
def wsr():
    return Mock(spec=web.WebSocketResponse)
