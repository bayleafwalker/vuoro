import os
import pytest
from .reference import ReferenceProvider
from .sprintctl_binding import SprintctlProvider


def pytest_addoption(parser):
    parser.addoption("--lease-pg-url", default=os.environ.get("LEASE_CONFORMANCE_PG_URL"))


def pytest_configure(config):
    config.addinivalue_line("markers", "essential_safety: LTD7.1 essential safety invariant")
    config.addinivalue_line("markers", "essential_workflow: LTD7.1 essential workflow")


def pytest_generate_tests(metafunc):
    if "provider" in metafunc.fixturenames:
        providers = ["reference"]
        if metafunc.config.getoption("--lease-pg-url") is not None:
            providers.append("sprintctl")
        metafunc.parametrize("provider", providers, indirect=True)


@pytest.fixture
def provider(request):
    if request.param == "reference":
        yield ReferenceProvider()
    else:
        url = request.config.getoption("--lease-pg-url")
        if not url:
            pytest.fail("configured PostgreSQL URL must not be empty")
        binding = SprintctlProvider(url)
        try:
            yield binding
        finally:
            binding.close()
