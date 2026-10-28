from __future__ import annotations

import os

from partners.base import PartnerClient


def make_partners(mock: bool | None = None, fixture: str | None = None) -> PartnerClient:
    use_mock = mock if mock is not None else os.environ.get("MOCK_PARTNERS", "0") == "1"
    if use_mock:
        from partners.mock import MockPartners

        return MockPartners(fixture=fixture)
    from partners.real import RealPartners

    return RealPartners()
