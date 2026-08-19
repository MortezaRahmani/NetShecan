# NetShecan — Authentication Flow Documentation

This document describes the complete authentication and token-refresh flows for the
providers supported by NetShecan: **Irancell**, **Shatel** (MyShatel) and **MCI**
(Hamrah-e-Aval / همراه‌من).

All flows were captured from the providers' own web applications and
implemented in `netshecan.py`. Values like `client_id`/`client_secret` are the
public constants the web clients use; the long-lived **refresh tokens are the
only stored secret** and are kept in `config.json`.

> ⚠️ All data in this document are examples.

---

## 1. High-level comparison

| | Irancell (`my.irancell.ir`) | Shatel (`my.shatel.ir` / `beta.my.shatel.ir`) | MCI (`my.mci.ir`) |
|---|---|---|---|
| Grant types | `otp-sms` (login), `refresh_token` | `authorization_code` + PKCE (login), `refresh_token` | OTP (login), `REFRESH_TOKEN` |
| Access token lifetime | **20 hours** (`expires_in=72000`) | **1 hour** (`expires_in=3600`) | **30 minutes** (`expires_in=1800`) |
| Refresh token lifetime | **~10 years** (JWT `exp` far in future) | **unknown/rotating** — rotated on every use | **~30 days** (`refresh_expires_in=2592000`) |
| Refresh token rotation | Rotated on every refresh | Rotated on every refresh | Rotated on every refresh |
| Token storage (web app) | `localStorage["NGMI-Sessions"]` | `localStorage["oidc.user:https://account-api.shatel.ir:MyShatelB2cWeb"]` | `localStorage["authToken"]` + `["refreshToken"]` |
| Auth header format | Raw JWT (no `Bearer ` prefix) | `Bearer <access_token>` | `Bearer <access_token>` |
| Login credential | Phone number + SMS OTP | Phone number + SMS OTP (via unified account portal) | Phone number + SMS OTP |
| Extra API headers | — | — | `version: 1.31.8`, `platform: WEB` |

---

## 2. Irancell

Base URL: `https://my.irancell.ir`

### 2.1 Login (one-time, done manually in the browser)

1. `POST /api/authorization/v1/login/options`
   ```json
   {"phone_number": "<phone_number>", "client_id": "<client_id>"}
   ```
   Response lists available methods:
   ```json
   [{"type": "phone_number", "value": "98930112233"},
    {"type": "email", "value": "r*******a@gmail.com"},
    {"type": "static_password", "value": "98930112233"},
    {"type": "phone_number_avr", "value": "98930112233"}]
   ```

2. `POST /api/authorization/v1/login/otp` — sends the SMS
   ```json
   {"phone_number": "98930112233", "notification_option": "phone_number", "client_id": "<client_id>"}
   ```
   Response: `{"message": "done"}`

3. `POST /api/authorization/v1/token` — exchange OTP for tokens
   ```json
   {
     "grant_type": "otp-sms",
     "password": "1206",
     "device_name": "Web Windows 10",
     "phone_number": "98930112233",
     "client_id": "<client_id>",
     "client_secret": "<client_secret>",
     "client_version": "9.77.1",
     "installation_id": "<uuid>"
   }
   ```
   Response:
   ```json
   {
     "access_token": "<jwt>",
     "refresh_token": "<jwt>",
     "preferred_language": "fa",
     "expires_in": "72000"
   }
   ```
   - `access_token` JWT claims include: `sub` (phone number), `client_id`,
     `installation_id`, `sjti` (session id), `exp` = iat + 72000s (**20 h**).
   - `refresh_token` JWT claims include: `phone_number`, `sub`, `sjti`,
     `exp` ≈ iat + 10 years.

### 2.2 Token refresh (automatic, implemented in NetShecan)

`POST /api/authorization/v1/token`

```json
{
  "grant_type": "refresh_token",
  "refresh_token": "<refresh_token>",
  "device_name": "Web Windows 10",
  "client_id": "<client_id>",
  "client_secret": "<client_secret>",
  "client_version": "9.77.1",
  "installation_id": "<uuid>"
}
```

Headers:
```
Authorization: <current access_token>   # REQUIRED, raw JWT, no "Bearer"
Content-Type:  application/json
Accept-Language: fa
```

Response: identical shape to login — new `access_token` (20 h) and a **rotated**
`refresh_token` (10 y). Save both; the old refresh token is invalidated server-side.

> ⚠️ If the `Authorization` header is missing → `400 "Authorization header is
> required for this grant type."` If it is not a validly-signed token of the same
> session → `401 Unauthorized`. NetShecan refreshes **5 minutes before expiry**
> while the current token is still valid, so this is never an issue.

### 2.3 Data endpoints

| Endpoint | Headers | Purpose |
|---|---|---|
| `GET /api/sim/v3/account` | `authorization: <access_token>` (raw, no `Bearer`) | Active data offers, cumulative remaining/total |

Relevant response fields:
- `active_offers[]`: `name` (Persian, e.g. `30روزه 20گیگابایت`), `is_gift`,
  `global_data_remaining` (MB), `total_amount` (MB), `expiry_date`, `start_date`
- `cumulative_amounts[]`: entries with `type: "data"` → `total` / `remained` (MB),
  used as the app's aggregate when `include_additional_packages` is **ON**.
  When **OFF** (the default), the headline remaining/total uses only the main
  (non-gift) offer instead.
- `main_account_balance`, `wow_charge`, `golden_charge`, `boom_plus` etc. — not used.

---

## 3. Shatel (MyShatel)

Shatel uses a centralized Identity Provider: **IdentityServer** at
`account-api.shatel.ir` issuing tokens for the API gateway `gateway.shatel.ir`.
The login is a full OAuth2 `authorization_code` + PKCE flow across three hosts.

### 3.1 Hosts

| Host | Role |
|---|---|
| `my.shatel.ir` | Old ASP.NET portal (HTML, server-rendered) |
| `account.shatel.ir` | Login/select-account UI |
| `account-api.shatel.ir` | IdentityServer: authorize, token, userinfo |
| `beta.my.shatel.ir` | New SPA (React + oidc-client) — recommended for API use |
| `gateway.shatel.ir` | Data API gateway (Bearer-protected) |

### 3.2 Login (one-time, done manually in the browser)

The flow starts at `https://my.shatel.ir/Account/FireLogin` (or directly on the
beta SPA) and redirects to `account.shatel.ir`:

1. `GET https://account.shatel.ir/login?returnUrl=<authorize-callback-url>`
2. Choose **OTP login** (`ورود با رمز یکبارمصرف`).
3. `POST /ui/v1.0/account/login/code/send`
   ```json
   {"codeReceiver": "09121112233", "codeReceiverType": "sms"}
   ```
   Response: `{..., "sendedToSms": true, "newRequestAfterSeconds": 60, "maskedPhone": "0912*****33"}`

4. Enter the SMS OTP → app calls `POST /ui/v1.0/account/login/code/verify`-style
   endpoint (UI step) and you reach `account.shatel.ir/select` — **pick one
   account** from the list. The selected account's `subjectId` is what the issued
   tokens bind to.
   - `GET /ui/v1.0/account/select/context` returns `childUserAccounts[]` with
     `description` (line number), `subjectId`, `usernames`, `fullName`.

5. Redirect back to the SPA callback with `?code=...&state=...&session_state=...`
   (e.g. `https://beta.my.shatel.ir/myshatel/login-callback`).

6. `POST https://account-api.shatel.ir/connect/token` — exchange the auth code:
   ```
   grant_type=authorization_code
   code=<code>
   redirect_uri=https://beta.my.shatel.ir/myshatel/login-callback
   code_verifier=<pkce_verifier>
   client_id=MyShatelB2cWeb
   ```
   Response:
   ```json
   {
     "id_token": "<jwt>",
     "access_token": "<jwt>",
     "expires_in": 3600,
     "token_type": "Bearer",
     "refresh_token": "<opaque>",
     "scope": "openid shatel.gateway.apiscope idsrv.local.apiscope offline_access"
   }
   ```

### 3.3 Token refresh (automatic, implemented in NetShecan)

`POST https://account-api.shatel.ir/connect/token`

```
grant_type=refresh_token
refresh_token=<refresh_token>
client_id=MyShatelB2cWeb
```

Headers:
```
Content-Type: application/x-www-form-urlencoded
Origin: https://beta.my.shatel.ir
```

Response: new `access_token` (1 h) + **rotated** `refresh_token` + `id_token`.
Save both; the old refresh token is consumed server-side
(`400 invalid_grant "Rejecting refresh token because it has been consumed already"`
if reused).

> ⚠️ The web app's `localStorage` keeps the token set from the *last successful
> login*, which is the **pre-rotation** refresh token. After you have refreshed
> once (e.g. while testing), grab the **current** token set from
> `localStorage["oidc.user:https://account-api.shatel.ir:MyShatelB2cWeb"]` on
> `beta.my.shatel.ir` — exactly as we did during capture.

### 3.4 Data endpoints

| Endpoint | Headers | Purpose |
|---|---|---|
| `GET /api/v1.0/myshatelB2cWeb/services/current` | `Authorization: Bearer <access_token>` | Current plan (duration, bandwidth, credit) |
| `GET /api/v1.0/myshatelB2cWeb/traffics/active-packages` | same | Active traffic packages + remaining/total |
| `GET /api/v1.0/myshatelB2cWeb/accounts/me` | same | Account identity (subject, customer id, title) |
| `GET /api/v1.0/myshatelB2cWeb/customers/me` | same | Customer info |
| `GET /api/v1.0/myshatelB2cWeb/banners`, `.../club/user/info` | same | UI extras — not used |

All gateway endpoints sit under `https://gateway.shatel.ir`.

**`services/current`** response (relevant fields):
```json
{
  "result": {
    "id": 11111,
    "name": "FairSilver-16384-FG-1",
    "durationInMonths": 1,
    "startDate": "2026-08-15T00:00:00",
    "endDate": "2026-09-14T23:59:59",
    "state": "InUse",
    "bandwidthMbps": 16,
    "totalTrafficCreditKb": 14680064,
    "monthlyTrafficCreditKb": 14680064,
    "rangeInfo": {"phoneNumber": "88225588"},
    "ipInfo": {"onlineSessionIpAddress": "100.100.100.100"}
  }
}
```

**`traffics/active-packages`** response (relevant fields):
```json
{
  "result": {
    "remainingKb": 22782285,
    "totalKb": 26214400,
    "packages": [
      {"id": 0, "name": "بسته پایه 15GB", "remainingKb": 13095251, "totalKb": 15728640,
       "expirationDate": "2026-09-15T00:00:00", "type": "Base", "inUse": true},
      {"id": 75570431, "name": "بسته 10GB روزانه", "remainingKb": 9687034, "totalKb": 10485760,
       "expirationDate": "2027-08-12T09:45:24+03:30", "type": "Timebound", "inUse": false}
    ]
  }
}
```

**Aggregation used by NetShecan:**
- aggregate remaining = `remainingKb` (all packages) = `22,782,285 KB` = **21.72 GB**
- aggregate total = `totalKb` = `26,214,400 KB` = **25 GB**
- used = total − remaining = **3.28 GB**
- main package = the one with `inUse: true` (Base); others listed under OTHER PACKAGES
- plan length (days) comes from `services/current.durationInMonths`

> 💡 The `include_additional_packages` setting (default **ON** for Shatel) controls
> whether the headline remaining/total uses the sum of **all** packages
> (`remainingKb`/`totalKb`) or only the main (`inUse`) package. The individual
> packages are always listed under OTHER PACKAGES regardless.

---

## 3.5 MCI (Hamrah-e-Aval / همراه‌من)

Base URL: `https://my.mci.ir`

The MCI web app is an **Angular SPA** (`my.mci.ir`) backed by a single API host
(`my.mci.ir/api`) with a JWT-based auth service (`idm`). Unlike the OIDC flow of
Shatel, MCI issues self-contained **HS256 JWTs** with simple `OTP` and
`REFRESH_TOKEN` credential types against the same endpoint.

### 3.5.1 Login (one-time, done manually in the browser)

1. `GET /auth` — landing page.
2. Enter the phone number → `POST /api/idm/v1/auth/send-otp`
   ```json
   {"username": "9121112233"}     // leading 0 dropped, no +98
   ```
   Headers: `version: 1.31.8`, `platform: WEB`, `Content-Type: application/json`
   Response: `{"otp": null}`

3. Enter the 5-digit SMS code → `POST /api/idm/v1/auth`
   ```json
   {"username": "9121112233", "credential": "38167", "credential_type": "OTP"}
   ```
   Response:
   ```json
   {
     "access_token": "<jwt>",
     "expires_in": 1800,
     "refresh_token": "<jwt>",
     "refresh_expires_in": 2592000,
     "session_state": "1111111_1111111_1111111"
   }
   ```
   - `access_token` (HS256): `sub` (customer id), `main_phone`, `current_phone`,
     `phone_numbers`, `sid`, `session_state`, `exp` = iat + 1800s (**30 min**).
   - `refresh_token` (HS256): `type: "Refresh"`, `main_phone`, `current_phone`,
     `session_state`, `exp` = iat + 2592000s (**30 days**).

### 3.5.2 Token refresh (automatic, implemented in NetShecan)

`POST /api/idm/v1/auth`

```json
{
  "username": "9121112233",
  "credential_type": "REFRESH_TOKEN",
  "credential": "<refresh_token>"
}
```

Headers: `Content-Type: application/json`, `Accept: application/json, text/plain, */*`,
`version: 1.31.8`, `platform: WEB`.

Response: identical shape to login — new `access_token` (30 min) and **rotated**
`refresh_token` (30 days). Save both; the old refresh token is invalidated
server-side.

### 3.5.3 Data endpoints

| Endpoint | Headers | Purpose |
|---|---|---|
| `GET /api/unit/v1/packages/details` | `Authorization: Bearer <access_token>`, `version: 1.31.8`, `platform: WEB`, `accept-language: en-GB` | Active packages + remaining/total (data, voice, SMS) |
| `GET /api/unit/v1/customer/units/campaign/campaign?brief=true` | same | Campaign banners — not used |
| `GET /api/bill/v1/invoices?op_type=mid` | same | Mid-period invoices — not used |

**`packages/details`** response (relevant fields, units are **GB**):
```json
{
  "packageItems": [
    {
      "type": "internet",
      "offerName": "بسته اینترنت یکماهه 10گیگابایت",
      "totalInitValue": 10.06,      // GB total
      "totalUnusedValue": 5.33,     // GB remaining
      "totalUnitName": "گیگ",
      "expireTime": "2026-09-11T18:22:44",
      "remainingDays": "24 روز باقی‌مانده",
      "packageStatus": "active",
      "itemDetails": [ { "offeringId": "502506", "unusedAmount": 5726366910, ... } ]
    }
  ],
  "totalInitBytes": 10.06,          // GB total (aggregate)
  "totalUnusedBytes": 5.33          // GB remaining (aggregate)
}
```

**Aggregation used by NetShecan:**
- aggregate remaining = `totalUnusedBytes` = **5.33 GB**
- aggregate total = `totalInitBytes` = **10.06 GB**
- used = total − remaining = **4.73 GB**
- main offer = the internet `packageItems[]` entry; `offerName` is Persian
  (e.g. `بسته اینترنت یکماهه 10گیگابایت`), converted to the `N Days - XGB` style
  using days until `expireTime`
- units: MCI reports **GB**; NetShecan converts to MB internally (×1024)

> 💡 The `include_additional_packages` setting (default **OFF** for MCI) controls
> whether the headline remaining/total uses the API aggregate
> (`totalInitBytes`/`totalUnusedBytes`, all internet packages) or only the first
> (main) `packageItems[]` entry.

---

## 4. What NetShecan stores in `config.json`

```json
{
  "provider": "irancell",              // active provider: "irancell" | "shatel" | "mci"
  "providers": {
    "irancell": {
      "authorization": "<access_token>",         // 20h; refreshed automatically
      "x_authorization_extra": "<refresh_token>",// 10y; rotated on each refresh
      "include_additional_packages": false,      // default OFF: main (non-gift) offer only
      "client_id": "<client_id>",
      "client_secret": "<client_secret>",
      "client_version": "9.77.1",
      "device_name": "Web Windows 10",
      "installation_id": "<uuid>",
      "accept": "application/json, text/plain, */*",
      "accept_language": "fa"
    },
    "shatel": {
      "refresh_token": "<refresh_token>",        // opaque; rotated on each refresh
      "access_token": "<access_token>",          // 1h; kept in memory/cache, refreshed automatically
      "include_additional_packages": true,       // default ON: sum of Base + additional packages
      "client_id": "MyShatelB2cWeb"
    },
    "mci": {
      "username": "9121112233",                  // leading 0 dropped
      "refresh_token": "<refresh_token>",        // ~30 days; rotated on each refresh
      "access_token": "<access_token>",          // 30 min; refreshed automatically
      "include_additional_packages": false,      // default OFF: first (main) internet package only
      "version": "1.31.8",
      "platform": "WEB",
      "accept_language": "en-GB"
    }
  },
  "poll_seconds": 300,
  "minimize_to_tray": true,
  "check_shecan": true,                          // global: run the Shecan DNS check
  "usage_threshold_mb": 300,
  "shecan_url": ""
}
```

### App settings (Settings dialog)

- **Provider** dropdown — switching it rebuilds the inputs for that provider
  (each provider exposes only its own auth fields: Irancell = access/refresh
  tokens + client params; Shatel = refresh/access tokens + client id; MCI =
  username + refresh/access tokens + version/platform).
- **Include Additional Data Packages** — per-provider checkbox controlling the
  headline remaining/total (see the aggregation notes for each provider above).
- **Auto Check Interval (minutes)** — global.
- **Usage Threshold Alert (MB)** — global.
- **Check Shecan** — global switch; when **OFF** the Shecan check is skipped
  entirely and its status row is hidden.
- **Shecan URL** — global (used for the Shecan status check).

### Refresh policy
- On every poll (default 5 min) and on startup, each provider checks its cached
  access token; if it expires within **5 minutes**, a refresh is triggered first.
- After a successful refresh the new tokens are written back to `config.json`
  immediately, so a later restart resumes with fresh tokens.
- On `401` (or `invalid_grant` from a consumed/expired refresh token) the app
  shows: **"Session expired - open Settings and paste a new token."**

### Re-seeding tokens when a refresh token dies
1. **Irancell**: log in at `my.irancell.ir`, then read
   `localStorage["NGMI-Sessions"]` → `list[active]` → `access_token` / `refresh_token`.
2. **Shatel**: log in on `beta.my.shatel.ir` (select account. e.g., **88225588**), then
   read `localStorage["oidc.user:https://account-api.shatel.ir:MyShatelB2cWeb"]`
   → `access_token` / `refresh_token`.
3. **MCI**: log in at `my.mci.ir/auth`, then read
   `localStorage["authToken"]` and `localStorage["refreshToken"]` (also fill the
   `username` — the login phone number without the leading `0`).
4. Paste the values into NetShecan's Settings dialog (or edit `config.json`).

---

## 5. Practical notes

- **No `Bearer` prefix for Irancell** — the web app sends the raw JWT in the
  `authorization` header. Shatel and MCI require `Bearer <token>`.
- **Units**: Irancell API returns **MB**; Shatel gateway returns **KB**; MCI
  returns **GB**. NetShecan normalizes all to MB internally (`gb() = MB/1024`).
- **Persian offer names** (Irancell) are converted to the `30 Days - 20GB` style
  by parsing the `Nروزه` / `Nگیگابایت` tokens; the trailing parenthetical
  (e.g. `(2 تا 7 صبح)`) is preserved. MCI names are converted using the number
  of days until `expireTime` and the package's total GB.
- **Account binding**: Shatel tokens bind to the selected account's `subjectId`.
  To switch accounts, re-login and select the other account; a token issued for
  one account cannot read another's data.
- **MCI username**: the `username` in MCI requests is the phone number with the
  leading `0` stripped (e.g. `9121112233`), matching the web app's own payloads.
- **Network access**: NetShecan bypasses system proxies (`ProxyHandler({})`) and
  retries transient failures with exponential backoff (3 attempts).