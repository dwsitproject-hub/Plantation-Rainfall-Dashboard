/* DWS Hub SDK loader: usage analytics, JavaScript errors and the "Report a bug" button,
 * which feed the Hub's Admin -> Analytic page and its Sentinel support agent.
 *
 * The Hub address and app key differ per environment, so they come from the backend
 * (/api/hub-sdk/config, from HUB_SDK_SCRIPT_URL / HUB_SDK_APP_KEY). Nothing is loaded
 * when they are not set. The SDK sends page paths only (never query strings), no cookies.
 */
(function () {
  'use strict';
  if (window.DwsHub && window.DwsHub.__loaded) return;
  // Calls made before the SDK script has loaded are queued and replayed by the SDK.
  window.DwsHub = window.DwsHub || [];

  fetch('/api/hub-sdk/config', { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
    .then(function (response) { return response.ok ? response.json() : null; })
    .then(function (config) {
      if (!config || !config.enabled) return;
      if (config.user) window.DwsHub.push(['identify', config.user]);  // Hub user id (OIDC sub) + email
      var script = document.createElement('script');
      script.src = config.scriptUrl;
      script.async = true;
      script.setAttribute('data-app-key', config.appKey);
      if (config.release) script.setAttribute('data-release', config.release);
      document.head.appendChild(script);
    })
    .catch(function () { /* analytics must never break the dashboard */ });

  /** Call on sign-out, before leaving the page: forgets the user and starts a new session. */
  window.plantRainfallHubReset = function () {
    var hub = window.DwsHub;
    if (hub && typeof hub.reset === 'function') hub.reset();
  };
})();
