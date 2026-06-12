"""
SorinFlow Divar Scraper - Stealth Configuration
Anti-detection measures for web scraping
"""
import random
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


@dataclass
class StealthConfig:
    """Configuration for stealth/anti-detection measures"""
    
    # Browser fingerprint settings
    viewport_width: int = 1920
    viewport_height: int = 1080
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False
    
    # Locale and timezone (Iran)
    locale: str = "fa-IR"
    timezone_id: str = "Asia/Tehran"
    
    # Geolocation (Tehran default)
    geolocation: Dict[str, float] = field(default_factory=lambda: {
        "latitude": 35.6892,
        "longitude": 51.3890,
        "accuracy": 100
    })
    
    # User agents pool (Updated for 2026)
    user_agents: List[str] = field(default_factory=lambda: [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ])
    
    # Delay settings (seconds)
    # کمی کاهش داده شده برای سرعت بیشتر ولی همچنان شبیه کاربر واقعی
    min_delay: float = 0.7
    max_delay: float = 1.8
    page_load_delay: float = 1.0
    scroll_delay: float = 0.2
    click_delay: float = 0.2
    typing_delay: float = 0.08

    # Scroll settings
    scroll_steps: int = 2
    scroll_distance_min: int = 100
    scroll_distance_max: int = 500
    
    # Request limits
    max_requests_per_minute: int = 20
    max_requests_per_session: int = 500
    
    def get_random_user_agent(self) -> str:
        """Get a random user agent from the pool"""
        return random.choice(self.user_agents)
    
    def get_random_delay(self) -> float:
        """Get a random delay between min and max"""
        return random.uniform(self.min_delay, self.max_delay)
    
    def get_random_scroll_distance(self) -> int:
        """Get a random scroll distance"""
        return random.randint(self.scroll_distance_min, self.scroll_distance_max)
    
    def get_viewport(self) -> Dict[str, int]:
        """Get viewport settings with slight randomization"""
        return {
            "width": self.viewport_width + random.randint(-50, 50),
            "height": self.viewport_height + random.randint(-50, 50)
        }


# Stealth JavaScript to inject
STEALTH_JS = """
// Overwrite the webdriver property
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// Overwrite the plugins property
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});

// Overwrite the languages property
Object.defineProperty(navigator, 'languages', {
    get: () => ['fa-IR', 'fa', 'en-US', 'en']
});

// Overwrite the platform property
Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32'
});

// Overwrite the hardwareConcurrency property
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8
});

// Overwrite the deviceMemory property
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8
});

// Overwrite chrome runtime
window.chrome = {
    runtime: {}
};

// Overwrite permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// Add touch support detection bypass
Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 0
});

// Console log warning bypass
const originalConsoleLog = console.log;
console.log = function(...args) {
    if (args[0] && typeof args[0] === 'string' && args[0].includes('devtools')) {
        return;
    }
    originalConsoleLog.apply(console, args);
};

// Disable automation detection
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
"""


def get_browser_args() -> List[str]:
    """Get Chrome browser arguments for stealth mode"""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-browser-side-navigation",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-extensions",
        "--disable-plugins-discovery",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-breakpad",
        "--disable-component-extensions-with-background-pages",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-domain-reliability",
        "--disable-hang-monitor",
        "--disable-ipc-flooding-protection",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-renderer-backgrounding",
        "--disable-sync",
        "--enable-features=NetworkService,NetworkServiceInProcess",
        "--force-color-profile=srgb",
        "--metrics-recording-only",
        "--no-first-run",
        "--password-store=basic",
        "--use-mock-keychain",
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--lang=fa-IR",
    ]


def get_context_options(stealth_config: StealthConfig, proxy: Optional[str] = None) -> Dict[str, Any]:
    """Get browser context options for stealth mode"""
    options = {
        "viewport": stealth_config.get_viewport(),
        "user_agent": stealth_config.get_random_user_agent(),
        "locale": stealth_config.locale,
        "timezone_id": stealth_config.timezone_id,
        "geolocation": stealth_config.geolocation,
        "permissions": ["geolocation"],
        "device_scale_factor": stealth_config.device_scale_factor,
        "is_mobile": stealth_config.is_mobile,
        "has_touch": stealth_config.has_touch,
        "java_script_enabled": True,
        "bypass_csp": True,
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    }
    
    if proxy:
        options["proxy"] = {"server": proxy}
    
    return options
