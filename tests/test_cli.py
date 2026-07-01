"""CLI and client tests for the Wodify Hermes tool."""

from __future__ import annotations

import httpx
from typer.testing import CliRunner

from wodify.cli import app
from wodify.client import LoginResult, WodifyClient

runner = CliRunner()


def test_login_success_persists_discovered_config(monkeypatch, tmp_path):
    import wodify.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")

    def fake_login(self, email, password, gym_subdomain=None):
        assert email == "test@example.com"
        assert password == "secret"
        assert gym_subdomain is None
        self.email = email
        self.password = password
        self.gym_subdomain = "delraybeach"
        self.base_url = "https://delraybeach.wodify.com"
        self.customer_id = "123"
        self.customer_hex = "abc"
        self.location_id = 12345
        self.version_hashes = {"moduleVersion": "m", "login": "l"}
        return LoginResult("u1", "g1", "abc", "123", "Test", 12345)

    monkeypatch.setattr(WodifyClient, "login", fake_login)

    result = runner.invoke(
        app,
        ["login", "--email", "test@example.com", "--password", "secret"],
    )

    assert result.exit_code == 0
    assert "Login successful as Test" in result.stdout
    saved = config.load_config()
    assert saved.gym_subdomain == "delraybeach"
    assert saved.base_url == "https://delraybeach.wodify.com"
    assert saved.customer_id == "123"
    assert saved.location_id == 12345


def test_client_defaults_to_delraybeach():
    client = WodifyClient()

    assert client.gym_subdomain == "delraybeach"
    assert client.base_url == "https://delraybeach.wodify.com"


def test_client_login_uses_wodify_outsystems_flow():
    requests: list[httpx.Request] = []

    manifest = {
        "manifest": {
            "urlVersions": {
                "/OnlineSalesPage/scripts/OnlineSalesPage.Common.UserInfo.mvc__a.js": "?a",
                "/OnlineSalesPage/scripts/OnlineSalesPage_CW.controller__b.js": "?b",
                "/OnlineSalesPage/scripts/OnlineSalesPage.Layouts.LayoutTop.mvc__c.js": "?c",
            }
        }
    }
    scripts = {
        "/OnlineSalesPage/scripts/OnlineSalesPage.Common.UserInfo.mvc__a.js": 'ServiceAPIGetSignInGlobalUserNameByEmail", "emailHash123"',
        "/OnlineSalesPage/scripts/OnlineSalesPage_CW.controller__b.js": 'ActionPrepare_LoginUser", "loginHash123"',
        "/OnlineSalesPage/scripts/OnlineSalesPage.Layouts.LayoutTop.mvc__c.js": 'DataActionGet_InitialData_InLayoutTop", "layoutHash123"',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/moduleversioninfo"):
            return httpx.Response(200, json={"versionToken": "moduleHash"})
        if path.endswith("/moduleinfo"):
            return httpx.Response(200, json=manifest)
        if path in scripts:
            return httpx.Response(200, text=scripts[path])
        if path.endswith("ServiceAPIGetSignInGlobalUserNameByEmail"):
            body = request.read()
            if b'"bootstrap"' in body:
                return httpx.Response(
                    403,
                    json={"ignored": True},
                    headers={"Set-Cookie": "nr2W_Theme_UI=crf%3Dcsrf-token%3Buid%3D0%3Bunm%3D; Path=/"},
                )
            assert request.headers["x-csrftoken"] == "csrf-token"
            assert b'"apiVersion":"emailHash123"' in body
            return httpx.Response(
                200,
                json={
                    "data": {
                        "Response": {
                            "ResponseGetSignInGlobalUserNameByEmail": {"Customer": "customerHex"},
                            "Error": {"HasError": False, "ErrorMessage": ""},
                        }
                    }
                },
            )
        if path.endswith("ActionPrepare_LoginUser"):
            body = request.read()
            assert b'"CustomerId":"0"' in body
            assert b'"apiVersion":"loginHash123"' in body
            return httpx.Response(
                200,
                json={
                    "data": {
                        "ErrorMessage": "",
                        "Response_ValidateLogin": {
                            "UserId": "user1",
                            "GlobalUserId": "global1",
                            "GlobalUserFirstName": "Test",
                            "CustomerId": "123",
                            "Customer": "customerHex2",
                            "ClientIsSuspended": False,
                            "CustomerIsSuspended": False,
                            "GlobalUserStatusId_IsActive": True,
                        },
                    }
                },
            )
        if path.endswith("DataActionGet_InitialData_InLayoutTop"):
            body = request.read()
            assert b'"apiVersion":"layoutHash123"' in body
            return httpx.Response(
                200,
                json={"data": {"ActiveLocations": {"List": [{"Id": 12345, "Name": "Test Gym"}]}}},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = WodifyClient({"membership_id": "existing-membership"}, client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.login("athlete@example.com", "secret")

    assert result.first_name == "Test"
    assert result.customer_id == "123"
    assert result.location_id == 12345
    assert client.session.authenticated is True
    assert client.version_hashes["emailLookup"] == "emailHash123"
    assert any(req.url.path.endswith("ActionPrepare_LoginUser") for req in requests)
    # A healthy login with no prior hashes must NOT register as drift. In
    # particular the session-bootstrap POST (which sends "bootstrap" versions)
    # must not trip server_version_mismatch.
    assert client.version_changed is False
    assert client.server_version_mismatch is False


def test_login_detects_version_change_without_failing():
    # Stored hashes that differ from what the live JS now serves.
    stored = {
        "moduleVersion": "OLDmodule",
        "login": "OLDlogin",
        "emailLookup": "emailHash123",     # unchanged
        "layoutTopInit": "layoutHash123",  # unchanged
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/moduleversioninfo"):
            return httpx.Response(200, json={"versionToken": "moduleHash"})
        if path.endswith("/moduleinfo"):
            return httpx.Response(
                200,
                json={"manifest": {"urlVersions": {
                    "/OnlineSalesPage/scripts/OnlineSalesPage.Common.UserInfo.mvc__a.js": "?a",
                    "/OnlineSalesPage/scripts/OnlineSalesPage_CW.controller__b.js": "?b",
                    "/OnlineSalesPage/scripts/OnlineSalesPage.Layouts.LayoutTop.mvc__c.js": "?c",
                }}},
            )
        scripts = {
            "/OnlineSalesPage/scripts/OnlineSalesPage.Common.UserInfo.mvc__a.js": 'ServiceAPIGetSignInGlobalUserNameByEmail", "emailHash123"',
            "/OnlineSalesPage/scripts/OnlineSalesPage_CW.controller__b.js": 'ActionPrepare_LoginUser", "loginHash123"',
            "/OnlineSalesPage/scripts/OnlineSalesPage.Layouts.LayoutTop.mvc__c.js": 'DataActionGet_InitialData_InLayoutTop", "layoutHash123"',
        }
        if path in scripts:
            return httpx.Response(200, text=scripts[path])
        if path.endswith("ServiceAPIGetSignInGlobalUserNameByEmail"):
            if b'"bootstrap"' in request.read():
                return httpx.Response(
                    403,
                    json={"ignored": True},
                    headers={"Set-Cookie": "nr2W_Theme_UI=crf%3Dcsrf-token%3Buid%3D0%3Bunm%3D; Path=/"},
                )
            return httpx.Response(200, json={"data": {"Response": {
                "ResponseGetSignInGlobalUserNameByEmail": {"Customer": "customerHex"},
                "Error": {"HasError": False, "ErrorMessage": ""},
            }}})
        if path.endswith("ActionPrepare_LoginUser"):
            return httpx.Response(200, json={"data": {
                "ErrorMessage": "",
                "Response_ValidateLogin": {
                    "UserId": "user1", "GlobalUserId": "global1",
                    "GlobalUserFirstName": "Test", "CustomerId": "123",
                    "Customer": "customerHex2", "ClientIsSuspended": False,
                    "CustomerIsSuspended": False, "GlobalUserStatusId_IsActive": True,
                }}})
        if path.endswith("DataActionGet_InitialData_InLayoutTop"):
            return httpx.Response(200, json={"data": {"ActiveLocations": {"List": [{"Id": 12345, "Name": "Test Gym"}]}}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = WodifyClient(
        {"membership_id": "existing-membership", "version_hashes": stored},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.login("athlete@example.com", "secret")

    # A version change is bookkeeping, not a failure — login still succeeds.
    assert result.first_name == "Test"
    assert client.session.authenticated is True
    # Changed hashes are detected (benign); the server did not reject a version
    # we sent, because login re-scrapes before making real calls.
    assert client.version_changed is True
    assert client.changed_endpoints == ["login", "moduleVersion"]
    assert client.server_version_mismatch is False
    assert client.drift_note().startswith(" NOTE: Wodify's version hashes changed")


def test_get_classes_parses_wodify_schedule():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("DataActionGetClassSchedule_InClasses")
        assert b'"SelectedDate":"2026-06-24"' in request.read()
        return httpx.Response(
            200,
            json={
                "data": {
                    "ClassSchedule": {
                        "List": [
                            {
                                "Class": {"Id": 1, "Name": "CrossFit", "StartDateTime": "2026-06-24T12:00:00Z"},
                                "Program": {"Id": "119335", "Name": "CrossFit"},
                            }
                        ]
                    }
                }
            },
        )

    client = WodifyClient(
        {
            "gym_subdomain": "delraybeach",
            "customer_id": "123",
            "customer_hex": "hex",
            "location_id": 12345,
            "version_hashes": {"moduleVersion": "m", "schedule": "s"},
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    # get_classes auto-logs-in when unauthenticated; this test drives the
    # schedule endpoint directly, so mark the session as already established.
    client.session.authenticated = True

    classes = client.get_classes(date="2026-06-24")

    assert len(classes) == 1
    assert classes[0].id == 1
    assert classes[0].program_id == 119335


def test_book_class_posts_wodify_booking_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("ActionBookClassWithExistingMembership")
        body = request.read()
        assert b'"ClassId":"123"' in body
        assert b'"SelectedMembershipId":"m1"' in body
        return httpx.Response(
            200,
            json={"data": {"InfoMessage": "Booked", "Error": {"HasError": False, "ErrorMessage": ""}}},
        )

    client = WodifyClient(
        {
            "gym_subdomain": "delraybeach",
            "membership_id": "m1",
            "version_hashes": {"moduleVersion": "m", "booking": "b"},
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.session.authenticated = True
    client.session.user_id = "u1"
    client.session.customer = "hex"

    result = client.book_class(123)

    assert result == {"success": True, "message": "Booked"}
