"""Isolated public-page captures and bounded, read-only navigation sessions."""

from contextlib import contextmanager
import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit

from ..config import Config


class BrowserStudyError(ValueError):
    pass


def public_origin(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise BrowserStudyError("Enter a public HTTPS product URL.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise BrowserStudyError("Use a public HTTPS URL without credentials or a custom port.")
    if re.search(r"[\s,\\]", parsed.hostname) or parsed.fragment.startswith("javascript:"):
        raise BrowserStudyError("This product URL is invalid.")
    return "https://" + parsed.hostname.encode("idna").decode("ascii").lower()


def public_address(origin):
    host = urlsplit(origin).hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as error:
        raise BrowserStudyError("The product hostname could not be resolved.") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise BrowserStudyError("Automatic browsing accepts public internet addresses only. Use a screenshot for a private app.")
    return sorted(addresses, key=lambda address: ":" in address)[0]


def viewport(value):
    value = value or {"width": 1280, "height": 800}
    if not isinstance(value, dict) or any(type(value.get(k)) is not int for k in ("width", "height")):
        raise BrowserStudyError("Choose a valid screen size.")
    if not 320 <= value["width"] <= 1920 or not 480 <= value["height"] <= 1200:
        raise BrowserStudyError("Screen size must be 320–1920 pixels wide and 480–1200 pixels tall.")
    return {k: value[k] for k in ("width", "height")}


@contextmanager
def public_browser(url, screen_size, asset_origins=()):
    from playwright.sync_api import sync_playwright, Error
    origin = public_origin(url)
    origins = {origin, *(public_origin(item) for item in asset_origins)}
    pins = {urlsplit(item).hostname: public_address(item) for item in origins}
    rules = [f"MAP {host} {('[' + ip + ']') if ':' in ip else ip}" for host, ip in pins.items()]
    rules.append("MAP * ~NOTFOUND")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", Config.PRODUCT_BROWSER_DIR)
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True, args=[
                "--host-resolver-rules=" + ", ".join(rules), "--no-proxy-server", "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ])
            context = browser.new_context(viewport=viewport(screen_size), locale="en-PK",
                timezone_id="Asia/Karachi", service_workers="block", accept_downloads=False,
                geolocation={"latitude": 24.8607, "longitude": 67.0011}, permissions=[])
            context.grant_permissions(["geolocation"], origin=origin)
            blocked = []

            def intercept(route):
                req = route.request
                parsed = urlsplit(req.url)
                req_origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
                unsafe_path = re.search(r"(?:^|/)(?:logout|signout|delete|publish|unsubscribe|checkout)(?:/|$)", parsed.path, re.I)
                document = req.resource_type == "document"
                if (req.method not in {"GET", "HEAD"} or req_origin not in origins
                        or (document and req_origin != origin) or unsafe_path):
                    blocked.append({"origin": req_origin[:200], "kind": req.resource_type, "reason": "read_only_or_origin_boundary"})
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", intercept)
            context.route_web_socket("**/*", lambda ws: ws.close())
            page = context.new_page()
            page.set_default_timeout(10_000)
            page.set_default_navigation_timeout(30_000)
            page.on("dialog", lambda dialog: dialog.dismiss())
            try:
                response = page.goto(url, wait_until="domcontentloaded")
                if response and response.status >= 400:
                    raise BrowserStudyError(f"The page returned HTTP {response.status}; use a publicly accessible screen.")
                handle = BrowserPage(page, origin, blocked)
                handle.settle()
                if public_origin(page.url) != origin:
                    raise BrowserStudyError("The page redirected to another origin. Enter the final product URL explicitly.")
                yield handle
            finally:
                context.close()
                browser.close()
    except Error as error:
        raise BrowserStudyError("The isolated browser could not load this page. Check its public URL, allowed asset origins, and browser installation.") from error


class BrowserPage:
    def __init__(self, page, origin, blocked):
        self.page, self.origin, self.blocked = page, origin, blocked

    def settle(self):
        from playwright.sync_api import TimeoutError
        try:
            self.page.wait_for_load_state("networkidle", timeout=12000)
        except TimeoutError:
            pass
        try:
            self.page.wait_for_function("""() => {
              const visible=e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&r.bottom>0&&r.top<innerHeight};
              const busy=[...document.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible);
              const loading=/(^|\\n)\\s*(?:loading|opening the map)(?:[. …]|$)/i.test(document.body.innerText);
              return !busy&&!loading&&[...document.images].filter(visible).every(i=>i.complete);
            }""", timeout=12000)
        except TimeoutError as error:
            raise BrowserStudyError("The page still shows a loading state. No participant was asked to evaluate it. Try again later or upload a ready screenshot.") from error
        critical = [item for item in self.blocked if item["kind"] in {"image", "stylesheet", "font"}]
        if critical:
            origins = ", ".join(sorted({item["origin"] for item in critical})[:4])
            raise BrowserStudyError("The page needs blocked visual assets from " + origins + ". Add these public asset origins or upload a complete screenshot.")

    def observe(self):
        links = self.page.locator("a[href]").evaluate_all("""els => els.filter(e => {
          const r=e.getBoundingClientRect(); return r.width && r.height && r.bottom>0 && r.top<innerHeight;
        }).slice(0,40).map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'Link').slice(0,160),url:e.href}))""")
        links = [item for item in links if len(item["url"]) <= 2048 and (item["url"].startswith(self.origin + "/") or item["url"] == self.origin)]
        visible_text = self.page.evaluate("""() => {
          const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT), out=[];
          let node; while(node=walker.nextNode()) {
            if(!node.textContent.trim()||node.parentElement.closest('script,style,noscript,[hidden],[aria-hidden="true"]'))continue;
            const range=document.createRange();range.selectNodeContents(node);const r=range.getBoundingClientRect();
            const style=getComputedStyle(node.parentElement);
            if(r.width&&r.height&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth&&style.visibility!=='hidden')out.push(node.textContent.trim());
          } return out.join('\\n').slice(0,14000);
        }""")
        return {"url": self.page.url, "text": visible_text, "links": links,
                "blocked_requests": list(self.blocked[-30:]), "title": self.page.title()[:200]}

    def screenshot(self):
        return self.page.screenshot(type="png", animations="disabled")

    def act(self, action, observation):
        kind = action.get("action")
        if kind == "open_link":
            index = action.get("link_index")
            if type(index) is not int or not 0 <= index < len(observation["links"]):
                raise BrowserStudyError("The participant selected a link that was not visible.")
            url = observation["links"][index]["url"]
            if re.search(r"/(?:login|signin|auth|compose|publish|checkout)(?:/|\?|$)", url, re.I):
                return "access_restriction"
            self.page.goto(url, wait_until="domcontentloaded")
        elif kind == "scroll":
            self.page.mouse.wheel(0, 550 if action.get("direction") == "down" else -550)
        elif kind == "click":
            x, y = action.get("x"), action.get("y")
            size = self.page.viewport_size
            if any(type(v) not in (float, int) for v in (x, y)) or not 0 <= x < size["width"] or not 0 <= y < size["height"]:
                raise BrowserStudyError("The participant selected a point outside the visible screen.")
            self.page.mouse.click(x, y)
        elif kind in {"finish", "blocked"}:
            return kind
        else:
            raise BrowserStudyError("The participant returned an unsupported browser action.")
        self.page.wait_for_timeout(700)
        self.settle()
        if public_origin(self.page.url) != self.origin:
            raise BrowserStudyError("Navigation left the configured product origin.")
        return "continue"


def capture_public(url, screen_size, asset_origins=()):
    with public_browser(url, screen_size, asset_origins) as browser:
        return browser.screenshot(), browser.observe()
