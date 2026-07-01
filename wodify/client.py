"""HTTP client core for Wodify Hermes."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping
from urllib.parse import unquote

import httpx

from .models import ClassInfo


DEFAULT_GYM_SUBDOMAIN = "delraybeach"
VIEW_NAME = "Main.Main"
APPLICATION_SOURCE_ID = 13

LOOKUP_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage/Common/UserInfo/ServiceAPIGetSignInGlobalUserNameByEmail"
LOGIN_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage_CW/ActionPrepare_LoginUser"
LAYOUT_TOP_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage/Layouts/LayoutTop/DataActionGet_InitialData_InLayoutTop"
SCHEDULE_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage/Screens/Classes/DataActionGetClassSchedule_InClasses"
CLASS_ACCESS_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage_CW/Classes/MembershipType/DataActionGetClassAccess_InMembershipType"
MEMBERSHIP_CLASS_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage_CW/Classes/MembershipType/DataActionGet_Class_InMembershipType"
BOOK_PATH = "/OnlineSalesPage/screenservices/OnlineSalesPage_CW/Classes/MembershipType/ActionBookClassWithExistingMembership"

# Backwards-compatible constants retained for older tests/imports. These are not
# real Wodify endpoints; live traffic uses the OutSystems screenservices above.
LOGIN_URL = "https://api.wodify.com/login"
LOGIN_URL_TEMPLATE = "https://{gym_subdomain}.wodify.com/login"
CLASSES_URL = "https://api.wodify.com/classes"
BOOK_URL = "https://api.wodify.com/book"

ENDPOINT_SOURCES: dict[str, dict[str, str]] = {
    "emailLookup": {
        "script": "OnlineSalesPage.Common.UserInfo.mvc",
        "pathFragment": "ServiceAPIGetSignInGlobalUserNameByEmail",
    },
    "login": {
        "script": "OnlineSalesPage_CW.controller",
        "pathFragment": "ActionPrepare_LoginUser",
    },
    "layoutTopInit": {
        "script": "OnlineSalesPage.Layouts.LayoutTop.mvc",
        "pathFragment": "DataActionGet_InitialData_InLayoutTop",
    },
    "schedule": {
        "script": "OnlineSalesPage.Screens.Classes.mvc",
        "pathFragment": "DataActionGetClassSchedule_InClasses",
    },
    "classAccess": {
        "script": "OnlineSalesPage_CW.Classes.MembershipType.mvc",
        "pathFragment": "DataActionGetClassAccess_InMembershipType",
    },
    "booking": {
        "script": "OnlineSalesPage_CW.Classes.MembershipType.mvc",
        "pathFragment": "ActionBookClassWithExistingMembership",
    },
    "membershipInit": {
        "script": "OnlineSalesPage_CW.Classes.MembershipType.mvc",
        "pathFragment": "DataActionGet_InitialData_InMembershipType",
    },
    "membershipClass": {
        "script": "OnlineSalesPage_CW.Classes.MembershipType.mvc",
        "pathFragment": "DataActionGet_Class_InMembershipType",
    },
    "membershipPlans": {
        "script": "OnlineSalesPage_CW.Classes.MembershipType.mvc",
        "pathFragment": "DataActionGet_ClassPlansAndPacks_InMembershipType",
    },
}


class WodifyDriftError(RuntimeError):
    """Raised when a Wodify response suggests stale version hashes."""

    def __init__(self, endpoint: str, hint: str) -> None:
        super().__init__(f"Wodify version drift on {endpoint}: {hint}")
        self.endpoint = endpoint
        self.hint = hint


@dataclass
class WodifySession:
    csrf_token: str = "bootstrap"
    user_id: str = ""
    global_user_id: str = ""
    customer: str = ""
    customer_id: str = "0"
    authenticated: bool = False
    first_name: str = ""


@dataclass
class LoginResult:
    user_id: str
    global_user_id: str
    customer: str
    customer_id: str
    first_name: str
    location_id: int | None = None


@dataclass
class WodifyClient:
    """Client for Wodify's OutSystems OnlineSalesPage screenservices."""

    config: Mapping[str, Any] | None = None
    base_url: str = ""
    client: httpx.Client | None = None
    timeout: float = 30.0
    session: WodifySession = field(default_factory=WodifySession, init=False)
    # Bookkeeping only: the scraped version hashes differ from the ones we had
    # stored. This is benign on its own (Wodify redeployed) — persist and move on.
    version_changed: bool = field(default=False, init=False)
    changed_endpoints: list[str] = field(default_factory=list, init=False)
    # A live (non-bootstrap) API call's response said the version we SENT was
    # stale. Only meaningful when paired with a failed/unhealthy response — then
    # it points at a breaking change on Wodify's side.
    server_version_mismatch: bool = field(default=False, init=False)
    session_initialized: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        cfg = dict(self.config or {})
        self.gym_subdomain = str(
            cfg.get("gym_subdomain")
            or cfg.get("gymSubdomain")
            or DEFAULT_GYM_SUBDOMAIN
        ).strip()
        configured_base = cfg.get("base_url") or cfg.get("baseUrl")
        self.base_url = (self.base_url or configured_base or f"https://{self.gym_subdomain}.wodify.com").rstrip("/")
        self.email = str(cfg.get("email") or "")
        self.password = str(cfg.get("password") or "")
        self.customer_id = str(cfg.get("customer_id") or cfg.get("customerId") or "0")
        self.customer_hex = str(cfg.get("customer_hex") or cfg.get("customerHex") or "")
        self.location_id = _optional_int(cfg.get("location_id") or cfg.get("locationId"))
        self.membership_id = str(cfg.get("membership_id") or cfg.get("membershipId") or "")
        self.version_hashes = _normalize_hashes(cfg.get("version_hashes") or cfg.get("versionHashes") or {})
        self.client = self.client or httpx.Client(follow_redirects=False, timeout=self.timeout)
        self.session.customer_id = self.customer_id
        self.session.customer = self.customer_hex

    @property
    def token(self) -> str | None:
        """Compatibility shim for the old scaffolded bearer-token client."""

        return self.session.user_id or None

    @token.setter
    def token(self, value: str | None) -> None:
        self.session.user_id = value or ""
        self.session.authenticated = bool(value)

    def has_version_drift(self) -> bool:
        """Deprecated alias for :attr:`version_changed`."""

        return self.version_changed

    def drift_note(self) -> str:
        """Human-readable note when hashes changed, else empty.

        Meant to be appended to an error message so a failure can be explained
        as a likely upstream breaking change rather than a bug in this tool.
        """

        if not (self.version_changed or self.server_version_mismatch):
            return ""
        detail = ", ".join(self.changed_endpoints) if self.changed_endpoints else "server rejected a sent version"
        return (
            f" NOTE: Wodify's version hashes changed ({detail}) — if this call "
            "failed, it is most likely a breaking change on Wodify's side, not a "
            "bug in this tool."
        )

    def _version_info(self, key: str) -> dict[str, str]:
        if not self.version_hashes or not self.version_hashes.get("moduleVersion"):
            self.version_hashes = discover_version_hashes(self.gym_subdomain, client=self.client)
        api_version = self.version_hashes.get(key) or ""
        if not api_version:
            raise WodifyDriftError(key, "missing apiVersion; run discovery again")
        return {"moduleVersion": self.version_hashes["moduleVersion"], "apiVersion": api_version}

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/OnlineSalesPage/Main",
            "x-csrftoken": self.session.csrf_token,
        }

    def _update_csrf_from_cookies(self) -> None:
        cookie = self.client.cookies.get("nr2W_Theme_UI", domain=f".{self.gym_subdomain}.wodify.com")
        if cookie is None:
            cookie = self.client.cookies.get("nr2W_Theme_UI")
        if not cookie:
            return
        for part in unquote(cookie).split(";"):
            key, _, value = part.partition("=")
            if key.strip() == "crf":
                self.session.csrf_token = value
                return

    def _post(self, path: str, body: Mapping[str, Any], *, raise_status: bool = True) -> dict[str, Any]:
        response = self.client.post(f"{self.base_url}{path}", headers=self._headers(), json=body)
        self._update_csrf_from_cookies()
        if raise_status:
            response.raise_for_status()
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {}
        version_info = data.get("versionInfo") if isinstance(data, dict) else None
        # Only trust real calls. The session-bootstrap POST deliberately sends
        # "bootstrap" as the version, so the server always reports a mismatch
        # there — counting it would be a permanent false positive.
        if raise_status and isinstance(version_info, dict) and (
            version_info.get("hasModuleVersionChanged") or version_info.get("hasApiVersionChanged")
        ):
            self.server_version_mismatch = True
        return data if isinstance(data, dict) else {}

    def init_session(self) -> None:
        if self.session_initialized:
            return
        self.client.cookies.set("osVisitor", str(uuid.uuid4()), domain=f".{self.gym_subdomain}.wodify.com")
        self.client.cookies.set("osVisit", str(uuid.uuid4()), domain=f".{self.gym_subdomain}.wodify.com")
        self._post(
            LOOKUP_PATH,
            {
                "versionInfo": {"moduleVersion": "bootstrap", "apiVersion": "bootstrap"},
                "viewName": VIEW_NAME,
                "inputParameters": {"Request": {"Email": ""}},
            },
            raise_status=False,
        )
        self.session_initialized = True

    def login(self, email: str | None = None, password: str | None = None, gym_subdomain: str | None = None) -> LoginResult:
        """Log in to Wodify and store the authenticated session cookies/state."""

        if gym_subdomain:
            self.gym_subdomain = gym_subdomain.strip()
            self.base_url = f"https://{self.gym_subdomain}.wodify.com"
        self.email = email or self.email
        self.password = password or self.password
        if not self.email or not self.password:
            raise ValueError("email and password are required")

        previous_hashes = dict(self.version_hashes)
        self.version_hashes = discover_version_hashes(
            self.gym_subdomain,
            existing=self.version_hashes,
            client=self.client,
        )
        # Benign bookkeeping: note which known hashes actually changed value.
        # (A hash appearing for the first time is not "drift".)
        self.changed_endpoints = sorted(
            key
            for key, value in self.version_hashes.items()
            if previous_hashes.get(key) and previous_hashes.get(key) != value
        )
        self.version_changed = bool(self.changed_endpoints)
        self.init_session()

        lookup = self._post(
            LOOKUP_PATH,
            {
                "versionInfo": self._version_info("emailLookup"),
                "viewName": VIEW_NAME,
                "inputParameters": {"Request": {"Email": self.email}},
            },
        )
        lookup_response = ((lookup.get("data") or {}).get("Response") or {})
        lookup_payload = lookup_response.get("ResponseGetSignInGlobalUserNameByEmail") or lookup_response
        lookup_error = lookup_response.get("Error") or {}
        if lookup_error.get("HasError"):
            raise RuntimeError(f"Email lookup failed: {lookup_error.get('ErrorMessage')}")
        customer_hex = lookup_payload.get("Customer")
        if not customer_hex:
            raise WodifyDriftError("emailLookup", "response missing Customer field")
        self.session.customer = customer_hex
        self.customer_hex = customer_hex

        login = self._post(
            LOGIN_PATH,
            {
                "versionInfo": self._version_info("login"),
                "viewName": VIEW_NAME,
                "inputParameters": {
                    "UserName": self.email,
                    "Password": self.password,
                    "ApplicationSourceId": APPLICATION_SOURCE_ID,
                    "CustomerId": self.customer_id or "0",
                    "SkipPasswordCheck": False,
                    "LoginToken": "",
                },
            },
        )
        data = login.get("data") or {}
        if data.get("ErrorMessage"):
            raise RuntimeError(f"Login failed: {data['ErrorMessage']}")
        user = data.get("Response_ValidateLogin") or {}
        if not user:
            raise WodifyDriftError("login", "response missing Response_ValidateLogin")
        if user.get("ClientIsSuspended") or user.get("CustomerIsSuspended"):
            raise RuntimeError("Account or gym is suspended")
        if not user.get("GlobalUserStatusId_IsActive", True):
            raise RuntimeError("Account is not active")

        self.session.user_id = str(user.get("UserId") or "")
        self.session.global_user_id = str(user.get("GlobalUserId") or "")
        self.session.customer = str(user.get("Customer") or self.customer_hex)
        self.session.customer_id = str(user.get("CustomerId") or self.customer_id or "0")
        self.session.first_name = str(user.get("GlobalUserFirstName") or "")
        self.session.authenticated = True
        self.customer_id = self.session.customer_id
        self.customer_hex = self.session.customer
        self.location_id = self.location_id or self.discover_location_id()
        if not self.membership_id or self.membership_id == "dummy-membership-id":
            self.membership_id = self.discover_membership_id() or self.membership_id

        return LoginResult(
            user_id=self.session.user_id,
            global_user_id=self.session.global_user_id,
            customer=self.session.customer,
            customer_id=self.session.customer_id,
            first_name=self.session.first_name,
            location_id=self.location_id,
        )

    def discover_location_id(self) -> int | None:
        client_variables = {
            "IsInMembershipsFlow": False,
            "CustomerId": self.customer_id,
            "LocationId": 0,
            "LoggedInGuardianId_Deprecated": "0",
            "Customer": self.customer_hex,
            "PrefilledEmail": "",
            "IsHeaderReady": True,
            "IsWebIntegration": False,
        }
        layout = self._post(
            LAYOUT_TOP_PATH,
            {
                "versionInfo": self._version_info("layoutTopInit"),
                "viewName": VIEW_NAME,
                "screenData": {"variables": {}},
                "clientVariables": client_variables,
            },
        )
        locations = (((layout.get("data") or {}).get("ActiveLocations") or {}).get("List") or [])
        if not locations:
            return None
        return _optional_int(locations[0].get("Id"))

    def get_classes(self, date: str | None = None, program_filter: str | None = None) -> List[ClassInfo]:
        if not self.session.authenticated:
            self.login()
        items = self._get_schedule_items(date, program_filter)
        return [_class_info_from_wodify(item) for item in items]

    def _get_schedule_items(self, selected_date: str | None = None, program_filter: str | None = None) -> list[dict[str, Any]]:
        if not selected_date:
            raise ValueError("date is required for live Wodify class lookup")
        if not self.location_id:
            raise ValueError("location_id is required; run login first")
        ids = [p.strip() for p in (program_filter.split(",") if program_filter else ["119335", "119416", "134852"]) if p.strip()]
        programs = [{"Value": program_id, "Label": "", "IsSelect": True, "ImageUrl": ""} for program_id in ids]
        selected = [{"Id": program_id} for program_id in ids]
        response = self._post(
            SCHEDULE_PATH,
            {
                "versionInfo": self._version_info("schedule"),
                "viewName": VIEW_NAME,
                "screenData": {
                    "variables": {
                        "ProgramsList": {"List": programs},
                        "SelectedProgramList": {"List": selected},
                        "EmployeesList": {"List": []},
                        "SelectedEmployeesList": {"List": [], "EmptyListItem": {"Id": "0"}},
                        "SelectedDate": selected_date,
                        "SelectedDate_WeekChange": selected_date,
                        "SelectedLocationId": self.location_id,
                        "LocationId": self.location_id,
                    }
                },
                "clientVariables": self._client_variables(),
            },
        )
        items = (((response.get("data") or {}).get("ClassSchedule") or {}).get("List") or [])
        return [item for item in items if isinstance(item, dict)]

    def discover_membership_id(self) -> str:
        if not self.session.authenticated:
            self.login()
        for days_ahead in range(1, 8):
            selected_date = (date.today() + timedelta(days=days_ahead)).isoformat()
            for item in self._get_schedule_items(selected_date):
                class_info = item.get("Class") or {}
                program = item.get("Program") or {}
                class_id = str(class_info.get("Id") or "")
                program_id = str(program.get("Id") or class_info.get("GymProgramId") or "")
                if not class_id or not program_id:
                    continue
                memberships = self.get_class_memberships(class_id, program_id)
                if memberships:
                    return str(memberships[0].get("Id") or "")
        return ""

    def get_class_memberships(self, class_id: str, program_id: str) -> list[dict[str, Any]]:
        membership_client_variables = {
            "PrefilledEmail": "",
            "LoggedIn_GlobalUserId": self.session.global_user_id,
            "LoggedIn_UserName": "",
            "BookedForListSerialized": "",
            "TokenForCreatePassword": "",
            "LoggedIn_UserId": self.session.user_id,
            "LoggedIn_LeadId": "0",
            "LoggedIn_CustomerId": self.customer_id,
            "OnlineMembershipSaleId": "0",
            "LoggedIn_Email": self.email,
        }
        screen_vars = {
            "FilterProgramId": program_id,
            "LoggedIn_UserId": self.session.user_id,
            "LoggedIn_GlobalUserId": self.session.global_user_id,
            "LoggedIn_Email": self.email,
            "Customer": self.session.customer,
            "LocationId": self.location_id,
            "ClassId": class_id,
            "HasProgramAccess": True,
            "SelectedMembershipId": "0",
            "ReservationOpenDateTime": datetime.now(timezone.utc).isoformat(),
            "BookWithNewMembershipClicked": False,
            "IsButtonLoading": False,
            "CustomerCountNoShowReservations": False,
            "ContractTerm": "Contract",
            "ShowBookingList": True,
            "IsToViewPurchaseOnly": False,
        }
        class_response = self._post(
            MEMBERSHIP_CLASS_PATH,
            {
                "versionInfo": self._version_info("membershipClass"),
                "viewName": VIEW_NAME,
                "screenData": {"variables": screen_vars},
                "clientVariables": membership_client_variables,
            },
        )
        if class_response.get("exception"):
            return []
        access_vars = {
            **screen_vars,
            "Get_Class_InMembershipType": class_response.get("data"),
            "_classIdInDataFetchStatus": 1,
            "_locationIdInDataFetchStatus": 1,
            "_customerInDataFetchStatus": 1,
            "_showBookingListInDataFetchStatus": 1,
            "_isToViewPurchaseOnlyInDataFetchStatus": 1,
            "_hasProgramAccessInDataFetchStatus": 1,
        }
        access_response = self._post(
            CLASS_ACCESS_PATH,
            {
                "versionInfo": self._version_info("classAccess"),
                "viewName": VIEW_NAME,
                "screenData": {"variables": access_vars},
                "clientVariables": membership_client_variables,
            },
        )
        memberships = (((access_response.get("data") or {}).get("MembershipsAvailable") or {}).get("List") or [])
        return [membership for membership in memberships if isinstance(membership, dict)]

    def book_class(self, class_id: int | str, program_id: int | None = None, dry_run: bool = False) -> Dict[str, Any]:
        if not self.session.authenticated:
            self.login()
        if not self.membership_id:
            raise ValueError("membership_id is required to book a class")
        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "message": f"[DRY RUN] would book class {class_id} using membership {self.membership_id}",
            }
        response = self._post(
            BOOK_PATH,
            {
                "versionInfo": self._version_info("booking"),
                "viewName": VIEW_NAME,
                "inputParameters": {
                    "Customer": self.session.customer,
                    "ClassId": str(class_id),
                    "ApplicationSourceId": APPLICATION_SOURCE_ID,
                    "UserId": self.session.user_id,
                    "SelectedMembershipId": self.membership_id,
                },
            },
        )
        data = response.get("data") or {}
        error = data.get("Error") or {}
        return {
            "success": not bool(error.get("HasError")),
            "message": error.get("ErrorMessage") or data.get("InfoMessage") or "Successfully booked!",
        }

    def _client_variables(self) -> dict[str, Any]:
        return {
            "IsInMembershipsFlow": False,
            "CustomerId": self.customer_id,
            "LocationId": self.location_id or 0,
            "LoggedInGuardianId_Deprecated": "0",
            "Customer": self.session.customer or self.customer_hex,
            "PrefilledEmail": "",
            "IsHeaderReady": True,
            "IsWebIntegration": False,
        }

    def config_updates(self) -> dict[str, Any]:
        return {
            "gym_subdomain": self.gym_subdomain,
            "base_url": self.base_url,
            "email": self.email,
            "password": self.password,
            "customer_id": self.customer_id,
            "customer_hex": self.customer_hex,
            "location_id": self.location_id,
            "membership_id": self.membership_id,
            "version_hashes": self.version_hashes,
        }


def discover_version_hashes(
    gym_subdomain: str = DEFAULT_GYM_SUBDOMAIN,
    existing: Mapping[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    base = f"https://{gym_subdomain}.wodify.com"
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    version_response = http.get(f"{base}/OnlineSalesPage/moduleservices/moduleversioninfo")
    version_response.raise_for_status()
    module_version = version_response.json()["versionToken"]

    info_response = http.get(f"{base}/OnlineSalesPage/moduleservices/moduleinfo")
    info_response.raise_for_status()
    url_versions = info_response.json()["manifest"]["urlVersions"]

    api_versions: dict[str, str] = {}
    for key, source in ENDPOINT_SOURCES.items():
        script_path = next(
            (path for path in url_versions if source["script"] in path and path.endswith(".js")),
            None,
        )
        if not script_path:
            continue
        script_response = http.get(f"{base}{script_path}")
        script_response.raise_for_status()
        escaped = re.escape(source["pathFragment"])
        match = re.search(rf'{escaped}[^\"]*\",\s*\"([A-Za-z0-9+=_-]{{10,}})\"', script_response.text)
        if match:
            api_versions[key] = match.group(1)

    old = _normalize_hashes(existing or {})
    return {
        "moduleVersion": module_version,
        "schedule": api_versions.get("schedule") or old.get("schedule", ""),
        "emailLookup": api_versions.get("emailLookup") or old.get("emailLookup", ""),
        "login": api_versions.get("login") or old.get("login", ""),
        "booking": api_versions.get("booking") or old.get("booking", ""),
        "classAccess": api_versions.get("classAccess") or old.get("classAccess", ""),
        "membershipInit": api_versions.get("membershipInit") or old.get("membershipInit", ""),
        "membershipClass": api_versions.get("membershipClass") or old.get("membershipClass", ""),
        "membershipPlans": api_versions.get("membershipPlans") or old.get("membershipPlans", ""),
        "layoutTopInit": api_versions.get("layoutTopInit") or old.get("layoutTopInit", ""),
    }


def discover_membership_id() -> str:
    """Deprecated compatibility wrapper; use WodifyClient.discover_membership_id."""

    return ""


def discover_config(gym_subdomain: str, email: str, password: str) -> Dict[str, Any]:
    client = WodifyClient({"gym_subdomain": gym_subdomain, "email": email, "password": password})
    client.login()
    return client.config_updates()


def _normalize_hashes(value: Mapping[str, Any] | str | None) -> dict[str, str]:
    if isinstance(value, str):
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(val) for key, val in value.items() if val is not None}


def _optional_int(value: Any) -> int | None:
    if value in (None, "", "<not discovered>"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _class_info_from_wodify(item: Mapping[str, Any]) -> ClassInfo:
    cls = item.get("Class") or {}
    program = item.get("Program") or {}
    return ClassInfo(
        id=int(cls.get("Id") or 0),
        name=str(cls.get("Name") or ""),
        start=str(cls.get("StartDateTime") or cls.get("StartTime") or ""),
        start_time=str(cls.get("StartTime") or ""),
        program_id=int(program.get("Id") or cls.get("GymProgramId") or 0),
        available=_optional_int(cls.get("Available")) or 0,
        class_limit=_optional_int(cls.get("ClassLimit")) or 0,
        reserved=_optional_int(cls.get("Reserved")) or 0,
        is_full=bool(cls.get("IsFull")),
        is_cancelled=bool(cls.get("IsCancelled")),
        allow_waitlist=bool(cls.get("AllowWaitlist")),
        waitlisted=_optional_int(cls.get("Waitlisted")) or 0,
    )
