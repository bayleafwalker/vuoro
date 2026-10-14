import os
import pytest
from .reference import ReferenceProvider
from .sprintctl_binding import SprintctlProvider
from .inbox_reference import ReferenceInboxProvider


def pytest_addoption(parser):
    parser.addoption("--lease-pg-url", default=os.environ.get("LEASE_CONFORMANCE_PG_URL"))
    parser.addoption("--inbox-pg-url", default=os.environ.get("INBOX_CONFORMANCE_PG_URL"))


def pytest_configure(config):
    config.addinivalue_line("markers", "conformance_gap(tracker): reproduced owner gap, never an invariant pass")
    config.addinivalue_line("markers", "essential_safety: LTD7.1 essential safety invariant")
    config.addinivalue_line("markers", "essential_workflow: LTD7.1 essential workflow")


def pytest_generate_tests(metafunc):
    if "inbox_provider" in metafunc.fixturenames:
        providers = ["reference"]
        if metafunc.config.getoption("--inbox-pg-url"):
            providers.append("sprintctl")
        metafunc.parametrize("inbox_provider", providers, indirect=True)
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


@pytest.fixture
def inbox_provider(request):
    if request.param == "reference":
        yield ReferenceInboxProvider()
        return
    from .inbox_sprintctl_binding import SprintctlInboxProvider, unavailable_reason
    url = request.config.getoption("--inbox-pg-url")
    reason = unavailable_reason(url)
    if reason:
        pytest.skip(reason)
    binding = SprintctlInboxProvider(url)
    try:
        yield binding
    finally:
        binding.close()
