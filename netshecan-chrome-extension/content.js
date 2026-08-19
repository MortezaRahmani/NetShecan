// NetShecan Helper — content script
// Reads provider tokens from localStorage and returns a JSON object
// matching the NetShecan per-provider config schema.

(function () {
  function jwtPayload(token) {
    try {
      const part = token.split(".")[1];
      const pad = part.replace(/-/g, "+").replace(/_/g, "/");
      return JSON.parse(atob(pad));
    } catch (e) {
      return null;
    }
  }

  function buildIrancell() {
    let s = null;
    try {
      s = JSON.parse(localStorage.getItem("NGMI-Sessions"));
    } catch (e) {
      s = null;
    }
    const list = s && s.list;
    const t = list && list.length ? list[s.active || 0] : null;
    if (!t || !t.access_token) return null;
    return {
      provider: "irancell",
      authorization: t.access_token,
      x_authorization_extra: t.refresh_token || "",
      client_version: "9.77.1",
      client_id: "4725a997e94b372b1c26e425086f4a17",
      client_secret: "7e9379a4d444a3c21cf28da6a032154dc4b644eba523e7684f71818dec3beeb7",
      device_name: "Web Windows 10",
      installation_id: localStorage.getItem("NGMI-InstallationId") || "",
      accept: "application/json, text/plain, */*",
      accept_language: "fa",
    };
  }

  function buildShatel() {
    const key = "oidc.user:https://account-api.shatel.ir:MyShatelB2cWeb";
    let o = null;
    try {
      o = JSON.parse(localStorage.getItem(key));
    } catch (e) {
      o = null;
    }
    if (!o || !o.access_token) return null;
    return {
      provider: "shatel",
      refresh_token: o.refresh_token || "",
      access_token: o.access_token,
      client_id: "MyShatelB2cWeb",
    };
  }

  function buildMci() {
    const at = localStorage.getItem("authToken");
    const rt = localStorage.getItem("refreshToken");
    if (!at) return null;
    const payload = jwtPayload(at) || {};
    const phone = (payload.main_phone || payload.current_phone || "").replace(/^0+/, "");
    return {
      provider: "mci",
      username: phone,
      refresh_token: rt || "",
      access_token: at,
      version: "1.31.8",
      platform: "WEB",
      accept_language: "en-GB",
    };
  }

  function build() {
    const host = location.hostname;
    if (host === "my.irancell.ir") return buildIrancell();
    if (host === "beta.my.shatel.ir") return buildShatel();
    if (host === "my.mci.ir") return buildMci();
    return null;
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    if (msg && msg.type === "NETSHECAN_GET") {
      sendResponse({ data: build() });
    }
    return false;
  });
})();
